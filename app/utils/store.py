import os
from datetime import date

import polars as pl

STORE_PATH = './data'


def get_file_path(name):
    file_date = date.today().strftime('%Y%m%d')
    return f'{STORE_PATH}/{name}.{file_date}.parquet'


def get_store(name):
    file = get_file_path(name)
    return pl.read_parquet(file) if os.path.exists(file) else None


def write_store(data, name):
    file = get_file_path(name)
    data.write_parquet(file, compression='zstd')
