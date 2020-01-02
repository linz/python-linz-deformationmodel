# Imports to support python 3 compatibility


class Error(ValueError):
    pass


class ModelDefinitionError(Error):
    pass


class InvalidValueError(Error):
    pass


class UndefinedValueError(Error):
    pass


class OutOfRangeError(UndefinedValueError):
    pass
