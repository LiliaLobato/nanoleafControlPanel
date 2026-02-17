# Nanoleaf Control Panel

The nanoleaf app is bad, like really really bad. Trash :skull: I was unable to connect to it, it turns off my lights at random and its buggy.

I been using the __Nanoleaf API__: https://forum.nanoleaf.me/docs/openapi for about a year and its amazing! But each time I want to change colors, I have to open my laptop and make quick http request.


## Expected final product
- Script to turn on/off a device based on the local sunset and my sleep time :zzz: (Ideally it should gradually turn on and off)
- Script to handle power consumption on large nanoleaf setups.
- Control panel to interact with the basic setup (change color, brightness, hue, etc) using an [esp32 cheap yellow display](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display).

## Content
__nanoleafLight__ and __nanoleafMicro__  are sub versions of the wrapper for the Nanoleaf OpenAPI. They provides the very basic functions available. 

__openWeather__ is a wrapper for the openWeather /weather endpoint. 

## Prerequisites
- You must know the IP address of the Nanoleaf device. Please refer to [Nanoleafapi Discovery()](https://nanoleafapi.readthedocs.io/en/latest/api.html#module-discovery) for help.
- You must know the latitude and lonngitude of your location. Please refer to [openWeather Geocoding API](https://openweathermap.org/api/geocoding-api?collection=other#direct) for help.
- You must have an authentication token for your nanoleaf device. Please refer to [Nanoleafapi generate_auth_token()](https://nanoleafapi.readthedocs.io/en/latest/methods.html#user-management) for help.
- You must have an authentication token for openWeather. Please refer to [OpenWeather api keys](https://home.openweathermap.org/api_keys) for help.