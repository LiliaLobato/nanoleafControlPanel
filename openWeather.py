"""openWeatherLight

This module is a light wrapper of the open weather API.

"""

import json, os, requests
from typing import Any, Dict
from dotenv import load_dotenv

class temperature:
    def __init__(self, rawData):        
        self.temperature = rawData["main"]["temp"]
        self.temperatureHuman = rawData["main"]["feels_like"]
        self.temperatureMin = rawData["main"]["temp_min"]
        self.temperatureMax = rawData["main"]["temp_max"]

class timezone:
    def __init__(self, rawData):        
        self.timezone = rawData["timezone"]
        self.sunrise = rawData["sys"]["sunrise"]
        self.sunset = rawData["sys"]["sunset"]

class weather:
    def __init__(self, rawData):   
        self.main = rawData["weather"][0]["main"]
        self.description = rawData["weather"][0]["description"]
        self.humidity = rawData["main"]["humidity"]
        self.clouds = rawData["clouds"]["all"]
        #self.rain = rain.1h 
        #self.snow = rain.1h
        self.weather = "test weather"

class openWeatherLight:
    def __init__(self, latitude, longitude, auth_token="", units="metric", print_errors : bool =True, full_debug : bool =False):
        self.longitude = longitude
        self.latitude = latitude
       
       
        self.auth_token = auth_token
        self.print_errors = print_errors
        self.full_debug = full_debug
        self.url = f"https://api.openweathermap.org/data/2.5/weather?lat={str(latitude)}&lon={str(longitude)}&units={str(units)}&appid={str(auth_token)}"

        rawData = self.__get_weatherAPI()
        self.timestamp = rawData["dt"]
        self.name = rawData["name"]
        self.temperature = temperature(rawData)
        self.timezone = timezone(rawData)
        self.weather = weather(rawData)
    
    def __get_weatherAPI(self) -> Dict[str, Any]:
        """Return the raw weather information 

        :returns: Dictionary of weather information, all fields
        """
        response = requests.get(self.url)
        return json.loads(response.text)


#important data in order to connect to openWeather
#https://openweathermap.org/current?collection=current_forecast
#sensitive information, never share or upload your keys 
script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_dir, '.env'))

LATITUDE = os.getenv('OPENWEATHER_LATITUDE')
LONGITUDE = os.getenv('OPENWEATHER_LONGITUDE')
AUTH_TOKEN = os.getenv('OPENWEATHER_AUTH_TOKEN')

print("hello world")

myWeather = openWeatherLight(LATITUDE, LONGITUDE, AUTH_TOKEN)

print(myWeather.weather.main)

