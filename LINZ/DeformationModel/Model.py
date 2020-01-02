# Imports to support python 3 compatibility


import os.path
import sys
import logging
import datetime
import time
import math
from collections import namedtuple

from .Error import Error, ModelDefinitionError, OutOfRangeError, UndefinedValueError
from .CsvFile import CsvFile
from .Time import Time
from .TimeModel import TimeModel
from .Grid import Grid
from .Cache import Cache


def _buildHash(x, attrs):
    return ":".join((str(getattr(x, a)) for a in attrs))


class TimeFunction(object):
    """
    The time submodel of a deformation model.  A wrapper around a time model managing also the 
    range of valid values for the model and for caching the calculated value
    """

    hashattr = ["time_function", "factor0", "time0", "factor1", "time1", "decay"]

    @staticmethod
    def hashKey(compdef):
        return _buildHash(compdef, TimeFunction.hashattr)

    @staticmethod
    def get(models, compdef):
        hash = TimeFunction.hashKey(compdef)
        if hash not in models:
            models[hash] = TimeFunction(compdef)
        return models[hash]

    def __init__(self, compdef):
        self.hash = self.hashKey(compdef)
        self.min_date = compdef.min_date
        self.max_date = compdef.max_date
        self.time_function = compdef.time_function
        self.time_complete = compdef.time_complete
        self.description = compdef.description
        self.calc_date = None
        self.base_calc_date = None
        self.calc_value = None
        self.calc_error_value = None
        self.calc_valid = True
        self.time0 = compdef.time0
        self.time1 = compdef.time1
        self.factor0 = compdef.factor0
        self.factor1 = compdef.factor1
        self.decay = compdef.decay
        self._model = TimeModel(
            compdef.time_function,
            compdef.factor0,
            compdef.time0,
            compdef.factor1,
            compdef.time1,
            compdef.decay,
        )

    def calcFactor(self, date, baseDate=None):
        if date != self.calc_date or baseDate != self.calc_base_date:
            self.calc_date = date
            self.calc_base_date = None
            self.calc_value = 0.0
            self.calc_error_value = 0.0
            ok = True

            for d in (baseDate, date):
                if d is None:
                    continue
                factor = 0.0
                if (self.min_date and d < self.min_date) or (
                    self.max_date and d > self.max_date
                ):
                    if not self.time_complete:
                        ok = False
                        break
                else:
                    factor = self._model.calcFactor(d)
                    logging.info(
                        "Time factor %s calculated at %s for %s", factor, d, self._model
                    )
                self.calc_value = factor - self.calc_value

            if not ok:
                self.calc_value = None
                self.calc_error_value = None
            else:
                self.calc_error_value = abs(self.calc_value)
                if self._model.squareVarianceFactor:
                    self.calc_error_value *= self.calc_error_value

        if self.calc_value is None:
            raise OutOfRangeError("Date outside valid range")
        return (self.calc_value, self.calc_error_value)

    def model(self):
        return self._model

    def __str__(self):
        return str(self._model)


class SpatialModel(object):
    """
    Manages the spatial model
    
    A spatial model may be invoked by multiple subcomponents in the 
    model definition component.csv file.  The hashKey function is used 
    to determine if two subcomponents use the same spatial model.  If so 
    then the model just adds time submodels.  This means the potentially
    expensive spatial submodel calculation needs only be performed once
    if necessary.

    Spatial and time calculations are cached to make calculating a set
    of deformations at the same time or a time series at a fixed location
    efficient.
    """

    hashattr = ["spatial_model", "file1"]
    checkattr = [
        "min_lon",
        "min_lat",
        "max_lon",
        "max_lat",
        "spatial_complete",
        "npoints1",
        "npoints2",
        "displacement_type",
        "description",
    ]

    @staticmethod
    def hashKey(submodel, compdef):
        return submodel + ":" + _buildHash(compdef, SpatialModel.hashattr)

    @staticmethod
    def compatibleDefinition(compdef1, compdef2):
        for attr in SpatialModel.checkattr:
            if getattr(compdef1, attr) != getattr(compdef2, attr):
                return False
        return True

    @staticmethod
    def get(model, models, submodel, compdef, load=False):
        hash = SpatialModel.hashKey(submodel, compdef)
        if hash not in models:
            models[hash] = SpatialModel(model, submodel, compdef, load)
        else:
            if not SpatialModel.compatibleDefinition(models[hash], compdef):
                raise ModelDefinitionError(
                    "Inconsistent usage of grid file "
                    + compdef.file1
                    + " in "
                    + submodel
                    + " component.csv"
                )
        return models[hash]

    def __init__(self, model, submodel, compdef, load=False):
        self.hash = SpatialModel.hashKey(submodel, compdef)
        self.min_lon = compdef.min_lon
        self.min_lat = compdef.min_lat
        self.max_lon = compdef.max_lon
        self.max_lat = compdef.max_lat
        self.spatial_complete = compdef.spatial_complete
        self.npoints1 = compdef.npoints1
        self.npoints2 = compdef.npoints2
        self.displacement_type = compdef.displacement_type
        self.error_type = compdef.error_type
        self.description = compdef.description
        self.columns = []
        if self.displacement_type in ["horizontal", "3d"]:
            self.columns.extend(["de", "dn"])
        if self.displacement_type in ["vertical", "3d"]:
            self.columns.append("du")
        if self.error_type in ["horizontal", "3d"]:
            self.columns.append("eh")
        if self.error_type in ["vertical", "3d"]:
            self.columns.append("ev")
        self.spatial_model = compdef.spatial_model
        self.file1 = os.path.join(submodel, compdef.file1)
        self.file2 = os.path.join(submodel, compdef.file2) if compdef.file2 else None
        self._model = None
        name = os.path.join(submodel, compdef.file1)
        self._name = name
        if compdef.spatial_model == "llgrid":
            self._model = Grid(
                model,
                self.file1,
                self.min_lon,
                self.max_lon,
                self.min_lat,
                self.max_lat,
                self.npoints1,
                self.npoints2,
                self.columns,
                name=name,
            )
            dlon, dlat = self._model.resolution()
            self._description = "Grid model ({0} x {1}) using {2}".format(
                dlon, dlat, self._name
            )
        else:
            raise ModelDefinitionError(
                "Invalid spatial model type " + compdef.spatial_model
            )

        # Cached calculations
        self._xy = (None, None)
        self._xydisp = [0.0] * 5
        self._xyInRange = True
        self._xyRangeError = None
        self._defUndefinedError = None

        self._modelError = None

        if load:
            self.load()

    def __str__(self):
        return self._description

    # So that this can be used as a spatial submodel set
    def models(self):
        yield self

    def load(self):
        """
        Spatial models are loaded on demand by default.  
        
        The load method forces loading of the spatial submodel 
        for validation.
        """
        try:
            self._model.load()
        except ModelDefinitionError:
            self._modelError = str(sys.exc_info()[1])
            raise

    def name(self):
        return self._name

    def model(self):
        return self._model

    def containsPoint(self, x, y):
        if x < self.min_lon or x > self.max_lon:
            return False
        if y < self.min_lat or y > self.max_lat:
            return False
        if self.spatial_model == "lltin":
            return self._model.containsPoint(x, y)
        return True

    def calcDeformation(self, x, y):
        logging.info("Calculating spatial component %s at (%f,%f)", self._name, x, y)
        if self._modelError:
            raise ModelDefinitionError(self._modelError)
        try:
            if (x, y) != self._xy:
                try:
                    self._xy = (x, y)
                    self._xyRangeError = None
                    self._defUndefinedError = None
                    self._xydisp = self._model.calcDeformation(x, y)
                    self._xyInRange = True
                except OutOfRangeError:
                    if self.spatial_complete:
                        self._xydisp = [0.0] * 5
                        self._xyInRange = False
                    else:
                        self._xyRangeError = sys.exc_info()[1]
                        raise
                except UndefinedValueError:
                    self._defUndefinedError = sys.exc_info()[1]
                    raise
                logging.info(
                    "Spatial component %s calculated as %s", self._name, self._xydisp
                )
            elif self._xyRangeError:
                raise OutOfRangeError(self._xyRangeError)
            elif self._defUndefinedError:
                raise UndefinedValueError(self._defUndefinedError)
            else:
                logging.info("Using cached spatial component %s", self._xydisp)

            return self._xydisp, self._xyInRange

        except ModelDefinitionError:
            self._modelError = str(sys.exc_info()[1])
            raise


class SpatialModelSet(object):
    """
    Manages a prioritised set of spatial models, such as a nested grid.  

    This provides the same interface as SpatialModel.
    """

    checkattr = ["version_added", "version_revoked", "displacement_type", "error_type"]

    @staticmethod
    def compatibilityHash(compdef):
        return (
            _buildHash(compdef, SpatialModelSet.checkattr)
            + ":"
            + _buildHash(compdef, TimeFunction.hashattr)
        )

    def __init__(self, submodel, model, compdef):
        self._component = submodel
        self._subcomponent = compdef.component
        self._models = [model]
        self._priorities = [compdef.priority]
        self._sortedModels = [model]
        self._baseModel = model
        self._checkhash = SpatialModelSet.compatibilityHash(compdef)
        self.min_lon = model.min_lon
        self.min_lat = model.min_lat
        self.max_lon = model.max_lon
        self.max_lat = model.max_lat
        self.displacement_type = compdef.displacement_type
        self.error_type = compdef.error_type

        # Cached calculations
        self._xy = (None, None)
        self._xydisp = [0.0] * 5
        self._xyInRange = True
        self._xyRangeError = None
        self._defUndefinedError = None
        self._modelError = None

    def addComponent(self, model, compdef):
        if SpatialModelSet.compatibilityHash(compdef) != self._checkhash:
            raise ModelDefinitionError(
                "Subcomponent "
                + str(self._component)
                + " of "
                + model.name()
                + " uses inconsistent versions, time models or displacement/error submodels"
            )

        self.min_lon = min(self.min_lon, model.min_lon)
        self.min_lat = min(self.min_lat, model.min_lat)
        self.max_lon = max(self.max_lon, model.max_lon)
        self.max_lat = max(self.max_lat, model.max_lat)
        self._models.append(model)
        self._priorities.append(compdef.priority)
        self._sortedModels = [
            self._models[i]
            for i in sorted(
                list(range(len(self._models))),
                key=lambda j: self._priorities[j],
                reverse=True,
            )
        ]
        self._baseModel = self._sortedModels[-1]

    def __str__(self):
        if len(self._models) == 1:
            return self._baseModel._description
        description = "Nested models:"
        for m in reversed(self._sortedModels):
            description = description + "\n" + str(m)
        return description

    def models(self):
        for m in reversed(self._sortedModels):
            yield m

    def load(self):
        """
        Spatial models are loaded on demand by default.  
        
        The load method forces loading of the spatial submodel 
        for validation.
        """
        try:
            for model in self._sortedModels:
                model.load()
        except ModelDefinitionError:
            self._modelError = str(sys.exc_info()[1])
            raise

    def name(self):
        return self._baseModel._name + " and subcomponents"

    def containsPoint(self, x, y):
        if x < self.min_lon or x > self.max_lon:
            return False
        if y < self.min_lat or y > self.max_lat:
            return False
        for model in reversed(self._sortedModels):
            if not model.containsPoint(x, y):
                return False
        return True

    def calcDeformation(self, x, y):
        logging.info("Calculating spatial component %s at (%f,%f)", self.name(), x, y)
        if self._modelError:
            raise ModelDefinitionError(self._modelError)
        try:
            if (x, y) != self._xy:
                self._xy = (x, y)
                self._xyRangeError = None
                self._defUndefinedError = None
                for m in self._sortedModels:
                    try:
                        self._xydisp, self._xyInRange = m.calcDeformation(x, y)
                    except OutOfRangeError:
                        logging.info("Spatial component %s out of range", self.name())
                        self._xyRangeError = sys.exc_info()[1]
                        raise
                    except UndefinedValueError:
                        logging.info("Spatial component %s undefined", self.name())
                        self._defUndefinedError = sys.exc_info()[1]
                        raise
                    if self._xyInRange:
                        break
                logging.info(
                    "Spatial component %s calculated as %s", self.name(), self._xydisp
                )
            elif self._xyRangeError:
                raise OutOfRangeError(self._xyRangeError)
            elif self._defUndefinedError:
                raise UndefinedValueError(self._defUndefinedError)
            else:
                logging.info(
                    "Using cached %s spatial component %s", self.name(), self._xydisp
                )

            return self._xydisp, self._xyInRange

        except ModelDefinitionError:
            self._modelError = str(sys.exc_info()[1])
            raise


class Component(object):
    """
    A model component combines a spatial and time submodel with a range of valid versions.
    The list of model components is used to compile the deformation model for any required version.
    """

    def __init__(self, submodel, compdesc, compdef, spatialModel, timeFunc):
        self.submodel = submodel
        self.compdesc = compdesc
        self.description = compdef.description
        self.versionAdded = compdef.version_added
        self.versionRevoked = compdef.version_revoked
        self.component = compdef.component
        self.priority = compdef.priority
        self.spatialModel = spatialModel
        self.name = spatialModel.name()
        self.timeFunction = timeFunc
        self.factor = 1.0
        self.timeFactor = 0.0
        self.timeErrorFactor = 0.0

    def appliesForVersion(self, version):
        """
        Test if the component applies to a specific version.
        """
        return self.versionAdded <= version and (
            self.versionRevoked == "0" or self.versionRevoked > version
        )

    def setFactor(self, factor):
        self.factor = factor

    def setDate(self, date, baseDate=None):
        """
        Calculate the deformation as a triple [de,dn,du] at a specific time and 
        location.  
        
        The time calculation is cached as the most common usage will be for
        many calculations at the same date.
        """
        logging.info(
            "Setting submodel %s date %s (base date %s)", self.name, date, baseDate
        )

        self.timeFactor, self.timeErrorFactor = self.timeFunction.calcFactor(
            date, baseDate
        )
        self.timeFactor *= self.factor
        self.timeErrorFactor *= self.factor
        logging.info("Time factor calculated as %s", self.timeFactor)

    def calcDeformation(self, x, y):
        """
        Calculate the deformation [de,dn,du,vh,vv] at a specified location.  
        Note that vh and vv are the variances horizonal and vertical if defined
        Assumes that the time function has already been calculated by a call to setDate
        """
        logging.info("Calculating submodel %s for location (%s,%s)", self.name, x, y)

        # If the time factor is 0 then don't need to do any more
        t0 = self.timeFactor
        if t0 == 0.0:
            logging.info("Time factor = 0.0 - spatial not calculated")
            return [0.0, 0.0, 0.0, 0.0, 0.0]

        t1 = self.timeErrorFactor
        value = self.spatialModel.calcDeformation(x, y)[0]
        return [
            value[0] * t0,
            value[1] * t0,
            value[2] * t0,
            value[3] * t1,
            value[4] * t1,
        ]


class Model(object):
    """
    Defines a deformation model which may have multiple versions and multiple submodels.  

    The model is loaded by specifying a base directory containing the files defining the model.  
    It can be used to calulate the deformation from any version of the model and at
    any time.  Also it can calculate the difference between two versions.

    The model is defined by a set of CSV (comma separated value) files.
    """

    versionspec = CsvFile.FieldSpec(
        "version",
        [
            "version \\d{8}",
            "release_date datetime",
            "reverse_patch boolean",
            "reason unicode",
        ],
    )

    modelspec = CsvFile.FieldSpec(
        "model",
        [
            "submodel \\w+",
            "version_added \\d{8}",
            "version_revoked (\\d{8}|0)",
            "reverse_patch boolean",
            "description unicode",
        ],
    )

    metadataspec = CsvFile.FieldSpec("metadata", ["item \\w+", "value unicode"])

    componentspec = CsvFile.FieldSpec(
        "submodel",
        [
            "version_added \\d{8}",
            "version_revoked (\\d{8}|0)",
            "reverse_patch boolean",
            "component int",
            "priority int",
            "min_lon float",
            "max_lon float",
            "min_lat float",
            "max_lat float",
            "spatial_complete boolean",
            "min_date datetime",
            "max_date datetime",
            "time_complete boolean",
            "npoints1 int",
            "npoints2 int",
            "displacement_type (horizontal|vertical|3d|none)",
            "error_type (horizontal|vertical|3d|none)",
            "max_displacement float",
            "spatial_model (llgrid|lltin)",
            "time_function (velocity|step|ramp|decay)",
            "time0 ?datetime",
            "factor0 ?float",
            "time1 ?datetime",
            "factor1 ?float",
            "decay ?float",
            r"file1 \w+\.csv",
            r"file2 ?\w+\.csv",
            "description unicode",
        ],
    )

    metadataitems = """
        model_name
        description
        version
        datum_code
        datum_name
        datum_epoch
        datum_epsg_srid
        ellipsoid_a
        ellipsoid_rf
        authority
        authority_website
        authority_address
        authority_email
        source_url
        """.split()

    def __init__(
        self,
        basedir,
        version=None,
        baseVersion=None,
        loadSubmodel=None,
        loadAll=False,
        useCache=True,
        clearCache=False,
    ):
        """
        Loads the deformation model located at the specified base directory (the
        directory holding the model.csv file).  If loadAll=True then the spatial submodels
        of all models are preloaded.  Otherwise they are loaded only when they are required 
        for calculations.

        The version and baseVersion for deformation calculations can be specified, otherwise
        the latest version is used by default.

        By default all submodels are loaded, but just one individual submodel can be
        selected.
        """
        logging.info("Loading deformation model from %s", basedir)
        self._basedir = basedir
        if not os.path.isdir(basedir):
            raise ModelDefinitionError(
                "Invalid deformation model base directory " + basedir
            )
        modfile = os.path.join(basedir, "model.csv")
        verfile = os.path.join(basedir, "version.csv")
        mtdfile = os.path.join(basedir, "metadata.csv")
        for f in (modfile, verfile, mtdfile):
            if not os.path.isfile(f):
                raise ModelDefinitionError(
                    "File " + modfile + " is missing from deformation model"
                )

        versions = {}
        curversion = None
        for ver in CsvFile("version", verfile, self.versionspec):
            if ver.version in versions:
                raise ModelDefinitionError(
                    "Version " + ver.version + " repeated in " + verfile
                )
            versions[ver.version] = ver
            if curversion is None or ver.version > curversion:
                curversion = ver.version
        self._versions = versions
        self._curversion = curversion

        metadata = {}
        for mtd in CsvFile("metadata", mtdfile, self.metadataspec):
            metadata[mtd.item] = mtd.value
        self._metadata = metadata

        for item in self.metadataitems:
            if item not in metadata:
                raise ModelDefinitionError(
                    "Metadata item " + item + " missing in " + mtdfile
                )

        mtdversion = str(metadata["version"])
        if mtdversion not in versions:
            raise ModelDefinitionError(
                "Version " + mtdversion + " from metadata is not defined in version.csv"
            )
        elif mtdversion != curversion:
            raise ModelDefinitionError(
                "Version "
                + mtdversion
                + " from metadata is not most recent version in version.csv"
            )

        self._name = str(metadata["model_name"])
        self._datumcode = str(metadata["datum_code"])
        self._datumname = str(metadata["datum_name"])
        self._ellipsoid = None
        try:
            self._datumsrid = int(metadata["datum_epsg_srid"])
        except:
            raise ModelDefinitionError("Invalid datum EPSG srid - must be an integer")
        try:
            self._datumepoch = Time.Parse(metadata["datum_epoch"])
        except:
            message = str(sys.exc_info()[1])
            raise ModelDefinitionError(
                "Invalid datum epoch in " + mtdfile + ": " + message
            )

        # List of model submodels, and hash of spatial files used to identify which have
        # already been loaded

        self._components = []
        self._spatial_models = {}
        self._time_functions = {}
        self._cache = None

        cacheFile = os.path.join(self._basedir, "cache.h5")
        if clearCache and os.path.exists(cacheFile):
            os.remove(cacheFile)
        if useCache:
            self._cache = Cache(cacheFile)

        # Submodels to use.  Default is all.  Specific submodels can be selected
        # as "submodel+...+submodel", or "-submodel+submodel+....+submodel"

        submodelList = []
        useList = True
        if loadSubmodel:
            if loadSubmodel.startswith("-"):
                useList = False
                loadSubmodel = loadSubmodel[1:]
            submodelList = loadSubmodel.lower().split("+")

        for mdl in CsvFile("model", modfile, self.modelspec):
            submodel = mdl.submodel
            if submodelList:
                matched = False
                for c in submodelList:
                    if submodel.lower() == c or submodel.lower().startswith(
                        "patch_" + c
                    ):
                        matched = True
                        break
                if matched:
                    if not useList:
                        continue
                elif useList:
                    continue

            if mdl.version_added not in versions:
                raise ModelDefinitionError(
                    "Submodel "
                    + mdl.submodel
                    + " version_added "
                    + mdl.version_added
                    + " is not in version.csv"
                )
            if mdl.version_revoked != "0" and mdl.version_revoked not in versions:
                raise ModelDefinitionError(
                    "Submodel "
                    + mdl.submodel
                    + " version_revoked "
                    + mdl.version_revoked
                    + " is not in version.csv"
                )
            compbase = os.path.join(basedir, submodel)
            if not os.path.isdir(compbase):
                raise ModelDefinitionError(
                    "Submodel " + mdl.submodel + " directory is missing"
                )
            compfile = os.path.join(compbase, "component.csv")
            if not os.path.isfile(compfile):
                raise ModelDefinitionError(
                    "Submodel " + mdl.submodel + " component.csv file is missing"
                )
            compname = os.path.join(submodel, "component.csv")

            filehashcheck = {}
            subcomponents = {}
            for compdef in CsvFile("component", compfile, self.componentspec):
                if compdef.version_added not in versions:
                    raise ModelDefinitionError(
                        "Submodel version_added "
                        + compdef.version_added
                        + " in "
                        + compname
                        + "is not in version.csv"
                    )
                if (
                    compdef.version_revoked != "0"
                    and compdef.version_revoked not in versions
                ):
                    raise ModelDefinitionError(
                        "Submodel version_revoked "
                        + compdef.version_revoked
                        + " in "
                        + compname
                        + " is not in version.csv"
                    )
                if compdef.displacement_type == "none" and compdef.error_type == "none":
                    raise ModelDefinitionError(
                        "Component in "
                        + compname
                        + " has displacement_type and error_type as none"
                    )

                spatial = SpatialModel.get(
                    self, self._spatial_models, submodel, compdef, loadAll
                )
                temporal = TimeFunction.get(self._time_functions, compdef)

                componentid = compdef.component

                if componentid > 0:
                    if componentid in subcomponents:
                        subcomponents[componentid].addComponent(spatial, compdef)
                        continue
                    else:
                        subcomp = SpatialModelSet(submodel, spatial, compdef)
                        subcomponents[componentid] = subcomp
                        spatial = subcomp

                self._components.append(
                    Component(submodel, mdl.description, compdef, spatial, temporal)
                )

        self._stcomponents = []
        self._version = ""
        self._baseVersion = ""
        self._versionName = ""
        self.setVersion(version, baseVersion)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def close(self):
        """
        Release resources to avoid circular links that will prevent garbage collection.
        """
        self._spatial_models = None
        self._time_functions = None
        self._components = None
        self._stcomponents = None
        if self._cache:
            self._cache.close()
            self._cache = None

    def setVersion(self, version=None, baseVersion=None):
        """
        Reset the version used for calculations.  If the version is None, then the current version 
        is used.
        
        If baseVersion is specified then the calculation will be the difference between the 
        version and the baseVersion. 
        """
        self._stcomponents = []
        if version is None:
            version = self._curversion
        else:
            version = str(version)
            if version not in self._versions:
                raise ValueError(
                    "Requested version "
                    + version
                    + " of deformation model is not defined"
                )

        if baseVersion:
            baseVersion = str(baseVersion)
            if baseVersion not in self._versions:
                raise ValueError(
                    "Requested base version "
                    + baseVersion
                    + " of deformation model is not defined"
                )
        for c in self._components:
            factor = 0
            if c.appliesForVersion(version):
                factor = 1
            if baseVersion and c.appliesForVersion(baseVersion):
                factor -= 1
            if factor != 0:
                c.setFactor(factor)
                self._stcomponents.append(c)

        vername = version
        if baseVersion:
            vername = version + "-" + baseVersion
        self._version = version
        self._baseVersion = baseVersion
        self._versionName = vername
        self._date = None
        self._baseDate = None
        self._timeRangeError = None

    def metadata(self, item):
        return self._metadata.get(item)

    def getFileName(self, *parts):
        return os.path.join(self._basedir, *parts)

    def _cacheMetadata(self, metadata, files):
        metadataparts = []
        for f in files:
            mtime = os.path.getmtime(self.getFileName(f))
            mtimestr = time.strftime("%Y%m%d%H%M%S", time.localtime(mtime))
            metadataparts.append(f.replace("\\", "/"))
            metadataparts.append(mtimestr)
        if metadata:
            metadataparts.extend([str(x) for x in metadata])
        return ":".join(metadataparts)

    def cacheData(self, file, metadata=None, files=None):
        if not self._cache:
            return None
        files = files or [file]
        metadata = self._cacheMetadata(metadata, files)
        file = file.replace("\\", "/")
        return self._cache.get(file, metadata)

    def setCacheData(self, data, file, metadata=None, files=None):
        if not self._cache:
            return
        files = files or [file]
        metadata = self._cacheMetadata(metadata, files)
        file = file.replace("\\", "/")
        self._cache.set(file, metadata, data)

    def setDate(self, date=None, baseDate=None):
        """
        Set the date when the deformation will be calculated, and optionally 
        a baseDate to calculate the difference in deformation between the date
        and baseDate.
        """
        if date is None:
            date = datetime.datetime.now()
        if date != self._date or baseDate != self._baseDate:
            self._date = date
            self._baseDate = baseDate
            self._timeRangeError = None
            try:
                for comp in self._stcomponents:
                    comp.setDate(date, baseDate)
            except OutOfRangeError:
                self._timeRangeError = sys.exc_info()[1]

    def calcDeformation(self, x, y, date=None, baseDate=None):
        """
        Calculate the deformation at a specified location.  The date and 
        baseDate can be set at the same time if required, otherwise the 
        values set with setDate will be used.
        """
        if not self._date or date is not None or baseDate is not None:
            self.setDate(date, baseDate)
        if self._timeRangeError:
            raise OutOfRangeError(self._timeRangeError)

        result = [0.0, 0.0, 0.0, 0.0, 0.0]
        for comp in self._stcomponents:
            compvalue = comp.calcDeformation(x, y)
            for i in range(5):
                result[i] += compvalue[i]
        result[3] = math.sqrt(abs(result[3]))
        result[4] = math.sqrt(abs(result[4]))
        return result

    def ellipsoid(self):
        if self._ellipsoid is None:
            from LINZ.Geodetic.Ellipsoid import Ellipsoid

            a = float(self._metadata["ellipsoid_a"])
            rf = float(self._metadata["ellipsoid_rf"])
            self._ellipsoid = Ellipsoid(a, rf)
        return self._ellipsoid

    def applyTo(
        self, lon, lat=None, hgt=None, date=None, baseDate=None, subtract=False
    ):
        """
        Applies the deformation to longitude/latitude coordinates.
        
        Input can be one of 
           lon,lat
           lon,lat,hgt
           [lon,lat],
           [lon,lat,hgt],
           [[lon,lat],[lon,lat]...]
           [[lon,lat,hgt],[lon,lat,hgt]...]

        For the first four cases returns a single latitude/longitude/height
        For the other cases returns an array of [lon,lat,hgt]

        The deformation is added to the coordinates unless subtract is True,
        in which case it is removed.
        """
        import numpy as np

        ell = self.ellipsoid()
        if lat is None:
            crds = lon
            if not isinstance(crds, np.ndarray):
                crds = np.array(crds)
            single = len(crds.shape) == 1
            if single:
                crds = crds.reshape((1, crds.size))
        else:
            single = True
            crds = [[lon, lat, hgt or 0]]

        results = []
        factor = -1 if subtract else 1
        for crd in crds:
            ln, lt = crd[:2]
            ht = crd[2] if len(crd) > 2 else 0
            deun = self.calcDeformation(ln, lt, date, baseDate)[:3]
            dedln, dndlt = ell.metres_per_degree(ln, lt)
            results.append(
                [
                    ln + factor * deun[0] / dedln,
                    lt + factor * deun[1] / dndlt,
                    ht + factor * deun[2],
                ]
            )
        return results[0] if single else np.array(results)

    def calcLLHFunc(self, lon, lat, hgt=0.0, subtract=False):
        """
        Returns a function which can take date as input and calculate the corresponding 
        LatLonHgt with the deformation model applied.
        """
        dedln, dndlt = self.ellipsoid().metres_per_degree(lon, lat)
        factor = -1 if subtract else 1

        def calcFunc(date):
            deun = self.calcDeformation(lon, lat, date)[:3]
            return [
                lon + factor * deun[0] / dedln,
                lat + factor * deun[1] / dndlt,
                hgt + factor * deun[2],
            ]

        return calcFunc

    def calcXYZFunc(self, XYZ, subtract=False):
        """
        Returns a function which can take date as input and calculate the corresponding 
        XYZ with the deformation model applied.
        """
        lon, lat, hgt = self.ellipsoid().geodetic(XYZ)
        llhfunc = self.calcLLHFunc(lon, lat, hgt, subtract)

        def calcFunc(date):
            lon, lat, hgt = llhfunc(date)
            return self.ellipsoid().xyz(lon, lat, hgt)

        return calcFunc

    def name(self):
        """
        Return the name of this model
        """
        return self._name

    def versionName(self):
        """
        Returns the name of the version of the model currently set
        """
        return self._versionName

    def version(self):
        """
        Returns the current version number
        """
        return self._version

    def baseVersion(self):
        """
        Returns the current version base number if calculating a difference, or None
        if not
        """
        return self._baseVersion

    def currentVersion(self):
        """
        Returns the current version of the model (ie the latest version)
        """
        return self._curversion

    def versions(self):
        """
        Returns a list of versions available
        """
        return sorted(self._versions.keys())

    def versionInfo(self, version):
        """
        Returns information from the versions.csv file for a specific version of the model
        """
        return self._versions[version]

    def datumName(self):
        """
        Returns the name of the datum defined in the model metadata
        """
        return self._datumname

    def datumCode(self):
        """
        Returns the datum code defined in the model metadata
        """
        return self._datumcode

    def datumEpoch(self):
        """
        Returns the datum epoch defined in the model metadata
        """
        return self._datumepoch

    def datumEpsgSrid(self):
        """
        Returns the EPSG srid (spatial reference id) of the datum
        latitude/longitude coordinate system
        """
        return self._datumsrid

    def components(self, allversions=False):
        compkey = lambda c: (
            0 if c.submodel == "ndm" else 1,
            c.versionAdded,
            c.submodel,
        )
        for c in sorted(self._components, key=compkey):
            if allversions or c.appliesForVersion(self.version()):
                yield c

    def reversePatchComponents(self, version=None):
        """
        Returns a list of components which contribute to a reverse patch
        for a specified datum version (default is for the current version) 
        """

        if version is None:
            version = self._version
        if version not in self._versions:
            raise RuntimeError("Invalid version {0} requested".format(version))

        ScaledComponent = namedtuple("ScaledComponent", "factor component")
        revcomps = []
        rpepoch = self._datumepoch
        for c in self.components(allversions=True):
            if c.versionAdded == version:
                factor = -1.0
            elif c.versionRevoked == version:
                factor = 1.0
            else:
                continue
            tf, ef = c.timeFunction.calcFactor(self._datumepoch)
            factor *= tf
            if factor != 0:
                revcomps.append(ScaledComponent(factor, c))
        return revcomps

    def description(self, allversions=False, submodels=True):
        """
        Return a description of the model
        """
        import io

        outs = io.StringIO()
        mtd = self._metadata
        outs.write("Deformation model: " + mtd["model_name"] + "\n")
        outs.write(
            "Datum: "
            + mtd["datum_name"]
            + " (reference epoch "
            + mtd["datum_epoch"]
            + ")\n"
        )
        outs.write("Version: " + self.version() + "\n")
        outs.write("\n")
        outs.write(mtd["description"] + "\n")

        if allversions:
            outs.write("\nVersions available:\n")
            for version in self.versions():
                v = self.versionInfo(version)
                outs.write(
                    "    "
                    + v.version
                    + " released "
                    + v.release_date.strftime("%d-%b-%Y")
                    + ": "
                    + v.reason
                    + "\n"
                )

        if submodels:
            compcount = {}
            for c in self.components(allversions):
                compcount[c.submodel] = (
                    compcount[c.submodel] + 1 if c.submodel in compcount else 1
                )

            outs.write("\nSubmodels:\n")
            lastcomponent = None
            for c in self.components(allversions):
                if c.submodel != lastcomponent:
                    description = c.compdesc.strip().replace("\n", "\n        ")
                    outs.write(
                        "\n    Submodel: " + c.submodel + ": " + description + "\n"
                    )
                    lastcomponent = c.submodel
                prefix = "    "
                if compcount[c.submodel] > 1:
                    description = c.description.strip().replace("\n", "\n            ")
                    outs.write("        Component: " + description + "\n")
                    prefix = "        "
                if allversions:
                    outs.write(prefix + "    Version added: " + c.versionAdded)
                    if c.versionRevoked != "0":
                        outs.write(" revoked: " + c.versionRevoked)
                    outs.write("\n")
                outs.write(prefix + "    Time function: " + str(c.timeFunction) + "\n")
                description = str(c.spatialModel).replace("\n", "\n        " + prefix)
                outs.write(prefix + "    Spatial model: " + description + "\n")

        description = outs.getvalue()
        outs.close()
        return description

    def __str__(self):
        return self.description(allversions=True)


def deformationModelArguments():
    """
    Creates an argument parser object use to load a deformation model with loadDeformationModel().
    The parser can be included as a parent for application program argument parsers. ie 

       myparser=ArgumentParser(description='Use deformation model, parents=[deformationModelArgments()]

    Adds the following arguments:

        -m modeldir       Directory in which deformation model is located
        --model-directory
        -r release        Release of deformation model to use, default is current model
        --model-release
        --model-components  Subcomponents of module to use
        --clear-model-cache  Clears the cache used by the deformation model 
        --ignore-model-cache Don't use the deformation model cache
        --list-deformation-model  Print a description of the deformation model to stdout and exit
    """
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "-m",
        "--model-directory",
        help="The directory in which  the deformation model is defined",
    )
    parser.add_argument(
        "-r",
        "--model-release",
        help="The release (version) of the deformation model to load",
    )
    parser.add_argument(
        "--model-components",
        help='The components of the deformation model to load (eg "ndm+patch_c1_20100904")',
    )
    parser.add_argument(
        "--clear-model-cache",
        action="store_true",
        help="Clear the model cache (force a reload)",
    )
    parser.add_argument(
        "--ignore-model-cache",
        action="store_true",
        help="Do not use the deformation model cache",
    )
    parser.add_argument(
        "--list-deformation-model",
        action="store_true",
        help="List out the deformation model and exit",
    )
    return parser


def loadDeformationModel(args):
    """
    Loads a deformation model based on the arguments.

        args      The result of parsing the command line using the arguments from 
                  addDeformationModelArguments
    """

    try:
        if args.model_directory is None:
            raise Error("Deformation model directory must be defined with -m parameter")
        model = Model(
            args.model_directory,
            version=args.model_release,
            loadSubmodel=args.model_components,
            useCache=not args.ignore_model_cache,
            clearCache=args.clear_model_cache,
        )
    except Error as e:
        if not args.list_deformation_model:
            raise
        if args.model_directory is not None:
            print("\nFailed to load deformation model from " + args.model_directory)
        print(e.message)
        sys.exit()

    if args.list_deformation_model:
        print(model.description(allversions=True))
        sys.exit()

    return model
