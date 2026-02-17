"""nanoleafLight

This module is a light version of the nanoleafapi wrapper for the Nanoleaf OpenAPI
It provides the basic functions available in the API. 
Some methods are debloted, others remain as original.

Refer to the full nanoleafapi wrapper for discovery, setup and advance functions.
https://github.com/MylesMor/nanoleafapi
"""

import requests
import json
from typing import Any, Dict, List

# Preset colours
RED = (255, 0, 0)
ORANGE = (255, 165, 0)
YELLOW = (255, 255, 0)
GREEN = (0, 255, 0)
LIGHT_BLUE = (173, 216, 230)
BLUE = (0, 0, 255)
PINK = (255, 192, 203)
PURPLE = (128, 0, 128)
WHITE = (255, 255, 255)

class nanoleafLight:
    def __init__(self, name, ip, auth_token="", port="16021", print_errors : bool =True, full_debug : bool =False):
        self.name = name
        self.ip = ip
        self.port = port
        self.auth_token = auth_token
        self.print_errors = print_errors
        self.full_debug = full_debug
        self.url = "http://" + ip + ":" + port +  "/api/v1/" + str(auth_token)

    def __error_check(self, code : int) -> bool:
            """Checks and displays error messages
                Determines the request status code. 
                Prints the error, if print_errors is true (default true)
                Print all, if full_debug is true (default false)
            
            :param code: The error code
            :returns: Returns True if request was successful, otherwise False
            """
            if self.print_errors:
                if code in (200, 204):
                    if self.full_debug: print(str(code) + ": Action performed successfully.")
                    return True
                if code == 400:
                    print("Error 400: Bad request.")
                elif code == 401:
                    print("Error 401: Unauthorized, invalid auth token. " +
                        "Please generate a new one.")
                elif code == 403:
                    print("Error 403: Unauthorized, please hold the power " +
                        "button on the controller for 5-7 seconds, then try again.")
                elif code == 404:
                    print("Error 404: Resource not found.")
                elif code == 500:
                    print("Error 500: Internal server error.")
                else:
                    print("Error " + str(code) + ": Huh...")
                return False
            return bool(code in (200, 204))

    def __str__(self):
        return f"{self.name}: {self.ip} - Auth setup: {self.isAuthTokenSetup()}"
    
    
    def isAuthTokenSetup(self) -> bool:
        """Simple check for authentication parameter.
        There is no validtion or token generation. 
        Please refer to full nanoleafapi for more info.
        
        :returns: True if auth tokeen is set, otherwise False
        """
        return bool(self.auth_token)


    #######################################################
    ####                  IDENTIFY                     ####
    #######################################################

    def identify(self) -> bool:
        """Runs the identify sequence on the lights
        :returns: True if successful, otherwise False
        """
        response = requests.put(self.url + "/identify")
        return self.__error_check(response.status_code)
    
    def check_heartbeat(self) -> bool:
        """Ensures there is a valid connection
        :returns: True if alive, otherwise False
        """
        response = requests.get(self.url, timeout=5)
        return self.__error_check(response.status_code)

    def get_info(self) -> Dict[str, Any]:
        """Identification data, usefull for logs and debug
        :returns: Dictionary of device information
        """
        response = requests.get(self.url)
        return json.loads(response.text)
    
    def get_fullLightStatus(self):
        # lights on/off
        # color mode
        # brightmness
        # saturation
        # hue
        # color temperature
        #effect
        return True

    #######################################################
    ####                    POWER                      ####
    #######################################################

    def power_off(self) -> bool:
        """Powers off the lights
        :returns: True if successful, otherwise False
        """
        data = {"on" : {"value": False}}
        response = requests.put(self.url + "/state", data=json.dumps(data))
        return self.__error_check(response.status_code)

    def power_on(self) -> bool:
        """Powers on the lights
        :returns: True if successful, otherwise False
        """
        data = {"on" : {"value": True}}
        response = requests.put(self.url + "/state", data=json.dumps(data))
        return self.__error_check(response.status_code)

    def get_power(self) -> bool:
        """Returns the power status of the lights
        :returns: True if on, False if off
        """
        response = requests.get(self.url + "/state/on")
        ans = json.loads(response.text)
        return ans['value']

    def toggle_power(self) -> bool:
        """Toggles the lights on/off"""
        if self.get_power():
            return self.power_off()
        return self.power_on()

    #######################################################
    ####               ADJUST BRIGHTNESS               ####
    #######################################################

    def set_brightness(self, brightness : int, duration : int =0) -> bool:
        """Sets the brightness of the lights

        :param brightness: The required brightness (between 0 and 100)
        :param duration: The duration over which to change the brightness

        :returns: True if successful, otherwise False
        """
        if brightness > 100 or brightness < 0:
            raise ValueError('Brightness should be between 0 and 100')
        data = {"brightness" : {"value": brightness, "duration": duration}}
        response = requests.put(self.url + "/state", data=json.dumps(data))
        return self.__error_check(response.status_code)

    def increment_brightness(self, brightness : int) -> bool:
        """Increments the brightness of the lights

        :param brightness: How much to increment the brightness, can
            also be negative

        :returns: True if successful, otherwise False
        """
        data = {"brightness" : {"increment": brightness}}
        response = requests.put(self.url + "/state", data = json.dumps(data))
        return self.__error_check(response.status_code)

    def get_brightness(self) -> int:
        """Returns the current brightness value of the lights"""
        response = requests.get(self.url + "/state/brightness")
        ans = json.loads(response.text)
        return ans['value']

    #######################################################
    ####                    HUE                        ####
    #######################################################

    def set_hue(self, value : int) -> bool:
        """Sets the hue of the lights

        :param value: The required hue (between 0 and 360)

        :returns: True if successful, otherwise False
        """
        if value > 360 or value < 0:
            raise ValueError('Hue should be between 0 and 360')
        data = {"hue" : {"value" : value}}
        response = requests.put(self.url + "/state", data=json.dumps(data))
        return self.__error_check(response.status_code)

    def increment_hue(self, value : int) -> bool:
        """Increments the hue of the lights

        :param value: How much to increment the hue, can also be negative

        :returns: True if successful, otherwise False
        """
        data = {"hue" : {"increment" : value}}
        response = requests.put(self.url + "/state", data=json.dumps(data))
        return self.__error_check(response.status_code)

    def get_hue(self) -> int:
        """Returns the current hue value of the lights"""
        response = requests.get(self.url + "/state/hue")
        ans = json.loads(response.text)
        return ans['value']

    #######################################################
    ####                 SATURATION                    ####
    #######################################################

    def set_saturation(self, value : int) -> bool:
        """Sets the saturation of the lights

        :param value: The required saturation (between 0 and 100)

        :returns: True if successful, otherwise False
        """
        if value > 100 or value < 0:
            raise ValueError('Saturation should be between 0 and 100')
        data = {"sat" : {"value" : value}}
        response = requests.put(self.url + "/state", data=json.dumps(data))
        return self.__error_check(response.status_code)

    def increment_saturation(self, value : int) -> bool:
        """Increments the saturation of the lights

        :param brightness: How much to increment the saturation, can also be
            negative.

        :returns: True if successful, otherwise False
        """
        data = {"sat" : {"increment" : value}}
        response = requests.put(self.url + "/state", data=json.dumps(data))
        return self.__error_check(response.status_code)

    def get_saturation(self) -> int:
        """Returns the current saturation value of the lights"""
        response = requests.get(self.url + "/state/sat")
        ans = json.loads(response.text)
        return ans['value']

    #######################################################
    ####              COLOUR TEMPERATURE               ####
    #######################################################

    def set_color_temp(self, value : int) -> bool:
        """Sets the white colour temperature of the lights

        :param value: The required colour temperature (between 0 and 100)

        :returns: True if successful, otherwise False
        """
        if value > 6500 or value < 1200:
            raise ValueError('Colour temp should be between 1200 and 6500')
        data = {"ct" : {"value" : value}}
        response = requests.put(self.url + "/state", json.dumps(data))
        return self.__error_check(response.status_code)

    def increment_color_temp(self, value : int) -> bool:
        """Sets the white colour temperature of the lights

        :param value: How much to increment the colour temperature by, can also
            be negative.

        :returns: True if successful, otherwise False
        """
        data = {"ct" : {"increment" : value}}
        response = requests.put(self.url + "/state", json.dumps(data))
        return self.__error_check(response.status_code)

    def get_color_temp(self) -> int:
        """Returns the current colour temperature of the lights"""
        response = requests.get(self.url + "/state/ct")
        ans = json.loads(response.text)
        return ans['value']

    #######################################################
    ####                 COLOUR MODE                   ####
    #######################################################

    def get_color_mode(self) -> str:
        """Returns the colour mode of the lights"""
        response = requests.get(self.url + "/state/colorMode")
        return json.loads(response.text)

    #######################################################
    ####                   EFFECTS                     ####
    #######################################################

    def get_current_effect(self) -> str:
        """Returns the currently selected effect

        If the name of the effect isn't available, this will return
        *Solid*, *Dynamic* or *Static* instead.

        :returns: Name of the effect or type if unavailable.
        """
        response = requests.get(self.url + "/effects/select")
        return json.loads(response.text)

    def set_effect(self, effect_name : str) -> bool:
        """Sets the effect of the lights

        :param effect_name: The name of the effect

        :returns: True if successful, otherwise False
        """
        data = {"select": effect_name}
        response = requests.put(self.url + "/effects", data=json.dumps(data))
        return self.__error_check(response.status_code)

    def list_effects(self) -> List[str]:
        """Returns a list of available effects"""
        response = requests.get(self.url + "/effects/effectsList")
        return json.loads(response.text)
