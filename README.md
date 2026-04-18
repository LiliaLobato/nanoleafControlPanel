# Nanoleaf Control Panel

The nanoleaf app is bad, like really really bad. Trash :skull: I was unable to connect to it, it turns off my lights at random and its buggy.

I been using the __Nanoleaf API__: https://forum.nanoleaf.me/docs/openapi for about a year and its amazing! But each time I want to change colors, I have to open my laptop and make quick http request.


## Expected final product
Fully automate the Nanoleaf light using cronJobs in a Rasphberry Pi.
- Script to turn on/off a device based on the local sunset/sundown and my sleep pattern :zzz: (Ideally it should gradually turn on and off).
  - When the day is cloudy, or raining it should start lights earlier.
  - The night light should have a warm hue tone with red undertones, cozy vibes.
  - The morning light should have a cold hue tone with blue undertones, wake me up energy vibes.
  - The sleep time should be configurable with default value 10:30pm and hard cutoff at 11pm
  - The wake up time should be configurable but the lights should be all up at 7am as default.
- Script to handle power consumption on large nanoleaf setups.
- Control panel to interact with the basic setup (change color, brightness, hue, etc) using an [esp32 cheap yellow display](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display).

## Future features
- Method to handle power consumption on large nanoleaf setups. The panels at the end of my setup flicker.

## Content
__nanoleafLight__ and __nanoleafMicro__  are sub versions of the wrapper for the Nanoleaf OpenAPI. They provide the very basic functions available. 

__openWeather__ is a wrapper for the openWeather /weather endpoint. 

## Prerequisites
- You must know the IP address of the Nanoleaf device. Please refer to [Nanoleafapi Discovery()](https://nanoleafapi.readthedocs.io/en/latest/api.html#module-discovery) for help.
- You must know the latitude and longitude of your location. Please refer to [openWeather Geocoding API](https://openweathermap.org/api/geocoding-api?collection=other#direct) for help.
- You must have an authentication token for your nanoleaf device. Please refer to [Nanoleafapi generate_auth_token()](https://nanoleafapi.readthedocs.io/en/latest/methods.html#user-management) for help.
- You must have an authentication token for openWeather. Please refer to [OpenWeather api keys](https://home.openweathermap.org/api_keys) for help.
