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
from typing import Dict, Any
import requests.packages.urllib3.util.connection as urllib3_connection

# Set up logging for the application
logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

class IPIntegration:
  def __init__(self, ipv4_api_url: str = None, ipv6_api_url: str = None) -> None:
    """
    Initialize the IPIntegration class.
    :param ip_api_url: The URL of the IP API to use. If None, defaults to "https://ipinfo.io/json".
    """
    if ipv4_api_url is None or ipv6_api_url is None:
        self.ipv4_api_url = "https://ipinfo.io/json"
        self.ipv6_api_url = "https://v6.ipinfo.io/json"
    else:
        self.ipv4_api_url = ipv4_api_url
        self.ipv6_api_url = ipv6_api_url

  def get_info(self, url: str) -> Dict[str, Any]:
      """
      Get information about an IP address.
      :param url: The URL of the IP API to use.
      :return: A dictionary containing the IP information.
      """
      try:
          response = requests.get(url)
          response.raise_for_status()
          return response.json()
      except requests.RequestException as e:
          logging.error(f"Error fetching IP info: {e}")
          logging.debug(traceback.format_exc())
          return {}

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
          logging.debug(traceback.format_exc())
          return {}

  def get_ipv6_info(self) -> Dict[str, Any]:
      """
      Get information about an IPv6 address.
      :return: A dictionary containing the IPv6 information.
      """
      # Override the allowed_gai_family method to use IPv6
      def allowed_gai_family():
          return socket.AF_INET6
      urllib3_connection.allowed_gai_family = allowed_gai_family
      try:
          ipv6 = self.get_info(self.ipv6_api_url)
          if not ipv6:
              logging.error("No IPv6 information found.")
              return {}
          return ipv6
      except requests.RequestException as e:
          logging.error(f"Error fetching IPv6 info: {e}")
          logging.debug(traceback.format_exc())
          return {}
