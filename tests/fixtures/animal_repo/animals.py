from typing import Any


class Animal:
    def make_noise(self):
        pass


class Dog(Animal):
    def make_noise(self):
        return make_noise()


def make_noise():
    return "woof"
