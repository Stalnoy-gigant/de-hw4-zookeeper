"""Общие настройки: подключение к RustFS (S3) и определение варианта."""
import boto3
from botocore.config import Config

ENDPOINT = "http://localhost:9000"
ACCESS_KEY = "rustfsadmin"
SECRET_KEY = "rustfsadmin"
BUCKET = "hh-lake"
CLEAN_KEY = "clean/vacancies.jsonl"      # компактный JSONL: все вакансии, без описаний
RAW_KEY = "raw/it_vacancies_2024.csv"    # исходный CSV как есть
RESULT_PREFIX = "results/"

# Вариант №1: «Программист, разработчик на 1С».
# Роль hh «Программист, разработчик» + упоминание 1С в названии вакансии или в ключевых навыках.
ROLE = "Программист, разработчик"
# Один и тот же шаблон используется в MapReduce (Python re) и в DuckDB (RE2) - синтаксис совместим.
NAME_RE = r"(^|[^a-zа-я0-9])1\s?[сc]($|[^a-zа-я])"   # 1С/1C в названии (кириллица и латиница)
SKILL_RE = r"^1\s?[сc]"                              # навык, начинающийся с 1С


def s3():
    return boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id=ACCESS_KEY,
                        aws_secret_access_key=SECRET_KEY, region_name="us-east-1",
                        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}))
