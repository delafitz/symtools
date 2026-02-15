import os

from massive import RESTClient

API_KEY = os.getenv('POLYGON_API_KEY')


def get_client():
    client = RESTClient(API_KEY)
    return client
