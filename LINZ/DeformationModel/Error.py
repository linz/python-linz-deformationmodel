
# Imports to support python 3 compatibility
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

class Error( ValueError ):
    pass

class ModelDefinitionError( Error ):
    pass

class InvalidValueError( Error ):
    pass

class UndefinedValueError( Error ):
    pass

class OutOfRangeError( UndefinedValueError ):
    pass

