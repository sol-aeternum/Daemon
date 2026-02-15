#!/usr/bin/env python3
from cryptography.fernet import Fernet


def generate_key():
    key = Fernet.generate_key()
    print(key.decode())


if __name__ == "__main__":
    generate_key()
