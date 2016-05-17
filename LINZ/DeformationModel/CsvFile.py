
# Imports to support python 3 compatibility
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import codecs
import os.path
import sys
import csv
import new
import re

from . import Time
from .Error import InvalidValueError, ModelDefinitionError

class CsvFile( object ):
    '''
    CSV reader class reads a CSV file for a specific set of fields,
    returning 
    '''

    class Field( object ):
        '''
        Defines a field specification - data type and validation
        '''
        
        def __init__( self, name, typestr, optionalfield=False ):
            optional = False
            if typestr.startswith('?'):
                optional = True
                typestr =typestr[1:]

            vre = None
            vtype = str
            if typestr == 'unicode':
                vtype=unicode
            elif typestr == 'int':
                vtype=int
            elif typestr == 'boolean':
                vre = re.compile(r'^[YN]$')
                vtype = lambda x: x == 'Y'
            elif typestr == 'float':
                vtype=float
            elif typestr == 'datetime':
                vtype = Time.Time.Parse
            else:
                vtype = str
                if typestr != 'str':
                    vre = re.compile(typestr)
                    typestr = str
            def f(x):
                if x is None or x=='':
                    if optional:
                        return None
                    else:
                        raise InvalidValueError("Missing value for "+name)
                if vre and not vre.match(x):
                    raise InvalidValueError('Invalid value "'+str(x)+'" for '+name)
                value = None
                try:
                    value = vtype(x)
                except:
                    raise InvalidValueError('Cannot convert '+name+' value "'+x+'" to '+typestr)
                return value
            self._name = name
            self._optional=optionalfield
            self.parse = f

        def name( self ):
            return self._name

        def optional( self ):
            return self._optional

        def parse( self, value ):
            raise InvalidValueError("Undefined field type")


    class FieldSpec( object ):
        '''
        Defines a csv file specification, a set of fields required in the file
        Creates a class which can parse and load a record
        '''
        def __init__( self, filetype, fields ):
            fieldlist = []
            arrays={}
            init=[]
            nfield = 0
            for f in fields:
                try:
                    name, typestr = f.split(' ')
                    fieldname=''
                    if '=' in name:
                        fieldname, name = name.split('=')
                    fieldname = fieldname or name
                    array=''
                    fieldoptional=False
                    if fieldname.endswith('?'):
                        fieldname=fieldname[:-1]
                        fieldoptional=True
                    if fieldname.endswith('[]'):
                        fieldname=fieldname[:-2]
                        if fieldname not in arrays:
                            array=True
                            arrays[fieldname]=0
                            init.insert(0,'  self.'+fieldname+'=[]\n')
                        else:
                            arrays[fieldname]+=1
                        array='['+str(arrays[fieldname])+']'

                    fieldlist.append(CsvFile.Field(name,typestr,fieldoptional))
                    valuestr='fieldlist['+str(nfield)+'].parse(record['+str(nfield)+'])'
                    nfield += 1

                    if array:
                        init.append('  self.'+fieldname+'.append('+valuestr+')\n')
                    else:
                        init.append('  self.'+fieldname+'='+valuestr+'\n')

                except InvalidValueError:
                    raise
                except:
                    raise InvalidValueError('Invalid field definition "'+f+'" in '+filetype+' file specification')

            cdef = ('class '+filetype+'(object):\n'+
                    ' def __init__(self,record):\n'+
                    ''.join(init)+
                    'container._class='+filetype+'\n')
            exec cdef in dict(fieldlist=fieldlist,container=self)
            self._fieldlist = fieldlist
            self._nfield = nfield

        def fieldlist( self ):
            return self._fieldlist

        def fieldnames( self ):
            return [f.name() for f in self._fieldlist]

        def parse( self, record  ):
            while len(record) < self._nfield:
                record.append('')
            return self._class(record)

    def __init__( self, filetype, filename, fields ):
        '''
        Initiallize the CsvFile reader.  

        filetype  is a name for the type of file, used in messages, and also
                  used as the name of the returned record type.
        filename  is the name of the file.
        fields    is the list of expected fields.
                  Each field is a space delimited string of "name type",
                  where type is one of int, float, datetime, str, unicode,
                  or a regular expression representing a string.
        '''
        if type(fields) != CsvFile.FieldSpec:
            fields = CsvFile.FieldSpec(filetype, fields)
        self._fields = fields

        if not os.path.isfile(filename):
            raise ValueError("CSV file "+filename+" doesn't exists")
        self._filetype = filetype
        self._filename = filename
        self._nrec = 0

        self._f = codecs.open(filename, encoding='utf8')
        self._csv = csv.reader(self._f)

        headers = self._csv.next()
        inserts=[]
        offset=0
        message=''
        for i,field in enumerate(self._fields.fieldlist()):
            if i+offset >= len(headers):
                missing=", ".join([f.name() for f in self.fields[i:] if not f.optional()])
                if missing:
                    message="Missing fields: "+missing
                else:
                    while i < len(self._fields.fieldlist()):
                        inserts.append(i)
                break
            if headers[i+offset]==field.name():
                continue
            if field.optional():
                inserts.append(i)
                offset -= 1
                continue
            message="Field "+headers[i+offset]+" does not match expected "+field.name()
            break

        if not message:
            extra=", ".join(headers[len(self._fields.fieldlist())+offset:])
            if extra:
                message="Extra fields: "+extra

        if message:
            raise ModelDefinitionError("File " + filename + " does not have the correct columns for a "+filetype+" file\n"+message)

        self._inserts=inserts
        self._ndata=len(self._fields.fieldlist())+offset

    def __iter__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self,etype,eval,tbk):
        return False

    def next(self):
        havedata = False
        # Skip blank lines
        while not havedata:
            data = self._csv.next()
            self._nrec+=1
            if len(data) > 1 or (len(data)==1 and data[0] != ''):
                break
        try: 
            while len(data) < self._ndata:
                data.append('')
            for i in self._inserts:
                data.insert(i,'')
            return self._fields.parse( data )
        except:
            error = str(sys.exc_info()[1])
            raise InvalidValueError( error+' in record '+str(self._nrec)+' of file '+self._filename)

        
