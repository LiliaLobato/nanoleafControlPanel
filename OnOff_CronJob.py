#Tecnicamente podria hacer un nanoleafMicro
import nanoleafLight, os
from dotenv import load_dotenv

#important data in order to connect to nanoleaf API
#https://forum.nanoleaf.me/docs/openapi
#sensitive information, never share or upload your keys 
script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_dir, '.env'))

NAME = os.getenv('NANOLEAF_NAME')
IP_ADDRESS = os.getenv('NANOLEAF_IP_ADDRESS')
AUTH_TOKEN = os.getenv('NANOLEAF_AUTH_TOKEN')

print("hello world")

pasillo = nanoleafLight.nanoleafLight(NAME, IP_ADDRESS, AUTH_TOKEN)

print(pasillo)

if pasillo.check_heartbeat():
    print("Im alive!")
    # we have a valid and live nanoleaf client