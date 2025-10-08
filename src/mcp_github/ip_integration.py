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

import socket
import logging
import requests
import traceback
from os import getenv
from typing import Dict, Any
import requests.packages.urllib3.util.connection as urllib3_connection

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

TIMEOUT = int(getenv('GITHUB_API_TIMEOUT', '5'))  # seconds, configurable via env

class IPIntegration:
  def __init__(self, ipv4_api_url: str = None, ipv6_api_url: str = None) -> None:
    """
    Initialize the IPIntegration class.

    :param ipv4_api_url: Optional custom API URL for IPv4 information.
    :param ipv6_api_url: Optional custom API URL for IPv6 information.
    """
    self.ipv4_api_url = None
    self.ipv6_api_url = None
    if ipv4_api_url is None or ipv6_api_url is None:
        self.ipv4_api_url = "https://ipinfo.io/json"
        self.ipv6_api_url = "https://v6.ipinfo.io/json"
    else:
        self.ipv4_api_url = ipv4_api_url
        self.ipv6_api_url = ipv6_api_url

  def get_info(self, url: str) -> Dict[str, Any]:
    """
    Fetches information from the specified URL using an HTTP GET request.
    Args:
        url (str): The URL to send the GET request to.
    Returns:
        Dict[str, Any]: The JSON response parsed into a dictionary if the request is successful.
        Returns an empty dictionary if the request fails or an exception occurs.
    Error Handling:
        Logs an error message and stack trace if a requests.RequestException is raised during the HTTP request.
    """
      
    try:
        response = requests.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Error fetching IP info: {e}")
        logging.debug(e)
        traceback.print_exc()
        return {"error": str(e)}

  def get_ipv4_info(self) -> Dict[str, Any]:
      """
      Get information about an IPv4 address.
      :return: A dictionary containing the IPv4 information.
      """
      try:
          ipv4 = self.get_info(self.ipv4_api_url)
          if not ipv4:
              logging.error("No IPv4 information found.")
              return {}
          return ipv4
      except requests.RequestException as e:
          logging.error(f"Error fetching IPv4 info: {e}")
          logging.debug(e)
          traceback.print_exc()
          return {"error": str(e)}

  def get_ipv6_info(self) -> Dict[str, Any]:
    """
    Retrieves IPv6 information from a specified API endpoint.
    This method temporarily overrides the `allowed_gai_family` method to force the use of IPv6 when making network requests.
    It then attempts to fetch IPv6-related information from the configured API URL.
    Returns:
        dict: A dictionary containing IPv6 information if the request is successful.
              Returns an empty dictionary if no information is found or if an error occurs.
    Error Handling:
        Logs an error message and returns an empty dictionary if a `requests.RequestException` is raised during the fetch operation.
        Also logs the full traceback at the debug level for troubleshooting.
    """
    try:
        urllib3_connection.allowed_gai_family = lambda: socket.AF_INET6
        ipv6 = self.get_info(self.ipv6_api_url)
        if not ipv6:
            logging.error("No IPv6 information found.")
            return {"error": "No IPv6 information found."}
        return ipv6
    except requests.RequestException as e:
        logging.error(f"Error fetching IPv6 info: {e}")
        logging.debug(e)
        traceback.print_exc()
        return {"error": f"Failed to fetch IPv6 information: {str(e)}"}
