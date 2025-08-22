#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# /*
#  * Copyright Said Sef
#  *
#  * Licensed under the Apache License, Version 2.0 (the "License");
#  * you may not use this file except in compliance with the License.
#  * You may obtain a copy of the License at
#  *
#  *      https://www.apache.org/licenses/LICENSE-2.0
#  *
#  * Unless required by applicable law or agreed to in writing, software
#  * distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.
#  */

import time
import logging
import threading
from os import getenv
from typing import Optional, Dict, Any, Callable
from functools import wraps
from http.server import HTTPServer, BaseHTTPRequestHandler
from prometheus_client import Counter, Histogram, Gauge, Summary, generate_latest, CollectorRegistry, CONTENT_TYPE_LATEST

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

class MetricsIntegration:
    """
    Prometheus metrics integration for the MCP GitHub PR Issue Analyser.
    
    This class provides comprehensive metrics collection including:
    - Total request count by tool/method
    - Request latency histograms
    - Active requests gauge
    - Response size summaries
    - Error count tracking
    """
    
    def __init__(self, registry: Optional[CollectorRegistry] = None):
        """
        Initialize the metrics integration.
        
        Args:
            registry: Optional custom registry for metrics collection
        """
        self.registry = registry or CollectorRegistry()
        self._setup_metrics()
        self._metrics_server: Optional[HTTPServer] = None
        self._metrics_thread: Optional[threading.Thread] = None
        
    def _setup_metrics(self):
        """Set up all Prometheus metrics."""
        # Total request count by tool/method
        self.request_count = Counter(
            'mcp_github_requests_total',
            'Total number of MCP tool requests',
            ['tool', 'status'],
            registry=self.registry
        )
        
        # Request latency histogram
        self.request_latency = Histogram(
            'mcp_github_request_duration_seconds',
            'Request duration in seconds',
            ['tool'],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0),
            registry=self.registry
        )
        
        # Active requests gauge
        self.active_requests = Gauge(
            'mcp_github_active_requests',
            'Number of currently active requests',
            ['tool'],
            registry=self.registry
        )
        
        # Response size summary
        self.response_size = Summary(
            'mcp_github_response_size_bytes',
            'Size of response in bytes',
            ['tool'],
            registry=self.registry
        )
        
        # Error count by tool and error type
        self.error_count = Counter(
            'mcp_github_errors_total',
            'Total number of errors',
            ['tool', 'error_type'],
            registry=self.registry
        )
        
        # Application info
        self.app_info = Gauge(
            'mcp_github_app_info',
            'Application information',
            ['version', 'name'],
            registry=self.registry
        )
        self.app_info.labels(version='2.6.1', name='mcp-github-pr-issue-analyser').set(1)

    def track_request(self, tool_name: str):
        """
        Decorator to track metrics for MCP tool requests.
        
        Args:
            tool_name: Name of the tool being tracked
            
        Returns:
            Decorator function
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Increment active requests
                self.active_requests.labels(tool=tool_name).inc()
                
                start_time = time.time()
                response_data = None
                status = 'success'
                
                try:
                    # Execute the original function
                    response_data = await func(*args, **kwargs)
                    
                    # Determine status from response
                    if isinstance(response_data, dict) and response_data.get('status') == 'error':
                        status = 'error'
                        # Track specific error types
                        error_msg = response_data.get('message', 'unknown_error')
                        error_type = self._classify_error(error_msg)
                        self.error_count.labels(tool=tool_name, error_type=error_type).inc()
                    
                    return response_data
                    
                except Exception as e:
                    status = 'error'
                    error_type = self._classify_error(str(e))
                    self.error_count.labels(tool=tool_name, error_type=error_type).inc()
                    raise
                    
                finally:
                    # Record metrics
                    duration = time.time() - start_time
                    self.request_latency.labels(tool=tool_name).observe(duration)
                    self.request_count.labels(tool=tool_name, status=status).inc()
                    self.active_requests.labels(tool=tool_name).dec()
                    
                    # Track response size if available
                    if response_data:
                        response_size = len(str(response_data).encode('utf-8'))
                        self.response_size.labels(tool=tool_name).observe(response_size)
                        
            return wrapper
        return decorator
    
    def _classify_error(self, error_message: str) -> str:
        """
        Classify error messages into types for better metrics.
        
        Args:
            error_message: The error message to classify
            
        Returns:
            Error type string
        """
        error_message_lower = error_message.lower()
        
        if 'authentication' in error_message_lower or 'token' in error_message_lower:
            return 'authentication_error'
        elif 'not found' in error_message_lower or '404' in error_message_lower:
            return 'not_found_error'
        elif 'permission' in error_message_lower or 'forbidden' in error_message_lower:
            return 'permission_error'
        elif 'timeout' in error_message_lower:
            return 'timeout_error'
        elif 'connection' in error_message_lower or 'network' in error_message_lower:
            return 'network_error'
        elif 'rate limit' in error_message_lower:
            return 'rate_limit_error'
        else:
            return 'unknown_error'

    def start_metrics_server(self, port: Optional[int] = None):
        """
        Start the Prometheus metrics HTTP server.
        
        Args:
            port: Port to run the metrics server on (default: 9090)
        """
        if self._metrics_server is not None:
            logging.warning("Metrics server is already running")
            return
            
        metrics_port = port or int(getenv('METRICS_PORT', '9090'))
        
        class MetricsHandler(BaseHTTPRequestHandler):
            def __init__(self, registry, *args, **kwargs):
                self.registry = registry
                super().__init__(*args, **kwargs)
                
            def do_GET(self):
                if self.path == '/metrics':
                    self.send_response(200)
                    self.send_header('Content-Type', CONTENT_TYPE_LATEST)
                    self.end_headers()
                    self.wfile.write(generate_latest(self.registry))
                elif self.path == '/health':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b'OK')
                else:
                    self.send_response(404)
                    self.end_headers()
                    
            def log_message(self, format, *args):
                # Suppress HTTP server logs to avoid clutter
                pass
        
        # Create a handler factory with the registry
        def handler_factory(*args, **kwargs):
            return MetricsHandler(self.registry, *args, **kwargs)
        
        try:
            self._metrics_server = HTTPServer(('0.0.0.0', metrics_port), handler_factory)
            self._metrics_thread = threading.Thread(
                target=self._metrics_server.serve_forever, 
                daemon=True,
                name='MetricsServer'
            )
            self._metrics_thread.start()
            logging.info(f"Prometheus metrics server started on port {metrics_port}")
            
        except Exception as e:
            logging.error(f"Failed to start metrics server on port {metrics_port}: {e}")
            self._metrics_server = None
            self._metrics_thread = None
            
    def stop_metrics_server(self):
        """Stop the Prometheus metrics HTTP server."""
        if self._metrics_server:
            self._metrics_server.shutdown()
            self._metrics_server.server_close()
            logging.info("Metrics server stopped")
            self._metrics_server = None
            self._metrics_thread = None

    def get_metrics(self) -> str:
        """
        Get the current metrics in Prometheus format.
        
        Returns:
            Metrics in Prometheus exposition format
        """
        return generate_latest(self.registry).decode('utf-8')