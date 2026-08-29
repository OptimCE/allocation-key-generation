from core.errors.errors import Error


# ---------------------------------------------------------------------------
# Auth (no domain code — use xxx)
# ---------------------------------------------------------------------------
class _AuthErrors:
    UNAUTHORIZED = Error(code=1, key="ERRORS.AUTH.UNAUTHORIZED")
    FORBIDDEN = Error(code=2, key="ERRORS.AUTH.FORBIDDEN")
    RATE_LIMITED = Error(code=3, key="ERRORS.AUTH.RATE_LIMITED")
    AUTHORIZATION_MISSING = Error(code=4, key="ERRORS.AUTH.AUTHORIZATION_MISSING")


class _SubscriptionErrors:
    NOT_SUBSCRIBED = Error(code=1003, key="ERRORS.SUBSCRIPTION.NOT_SUBSCRIBED")


class _GenerationErrors:
    GET_GENERATIONS = Error(code=2000, key="ERRORS.GENERATION.GET_GENERATIONS")
    GET_ALLOCATION_KEYS = Error(code=2001, key="ERRORS.GENERATION.GET_ALLOCATION_KEYS")
    GET_ALLOCATION_KEY = Error(code=2002, key="ERRORS.GENERATION.GET_ALLOCATION_KEY")
    ALLOCATION_KEY_NOT_FOUND = Error(code=2003, key="ERRORS.GENERATION.ALLOCATION_KEY_NOT_FOUND")
    SAVE_KEY = Error(code=2004, key="ERRORS.GENERATION.SAVE_KEY")
    DELETE_GENERATION = Error(code=2005, key="ERRORS.GENERATION.DELETE_GENERATION")
    DELETE_KEY = Error(code=2006, key="ERRORS.GENERATION.DELETE_KEY")
    GENERATION_NOT_FOUND = Error(code=2007, key="ERRORS.GENERATION.GENERATION_NOT_FOUND")
    GET_ALGORITHMS = Error(code=2008, key="ERRORS.GENERATION.GET_ALGORITHMS")
    GET_ALGORITHM_INPUT = Error(code=2009, key="ERRORS.GENERATION.GET_ALGORITHM_INPUT")
    ALGORITHM_NOT_FOUND = Error(code=2010, key="ERRORS.GENERATION.ALGORITHM.NOT_FOUND")
    START_GENERATION = Error(code=2011, key="ERRORS.GENERATION.START_GENERATION")
    INVALID_ALGORITHM_INPUTS = Error(code=2012, key="ERRORS.GENERATION.INVALID_ALGORITHM_INPUTS")
    STORAGE_UPLOAD_FAILED = Error(code=2013, key="ERRORS.GENERATION.STORAGE_UPLOAD_FAILED")
    INVALID_FILE = Error(code=2014, key="ERRORS.GENERATION.INVALID_FILE")
    FILE_TOO_LARGE = Error(code=2015, key="ERRORS.GENERATION.FILE_TOO_LARGE")

    # --- CRM-sourced input (source = DataSource.CRM) -----------------------
    # Raised before a run is queued (pre-flight on POST /from-crm) and again
    # in the worker, which re-reads the data at execution time.
    INVALID_PERIOD = Error(code=2016, key="ERRORS.GENERATION.INVALID_PERIOD")
    SHARING_OPERATION_NOT_FOUND = Error(
        code=2017, key="ERRORS.GENERATION.SHARING_OPERATION_NOT_FOUND"
    )
    CRM_NO_DATA = Error(code=2018, key="ERRORS.GENERATION.CRM_NO_DATA")
    # No unique constraint on meter_consumption(ean, timestamp): a repeated
    # import silently doubles a participant's energy, so this is fatal rather
    # than a warning.
    CRM_DUPLICATE_READINGS = Error(code=2019, key="ERRORS.GENERATION.CRM_DUPLICATE_READINGS")
    CRM_NO_INJECTION = Error(code=2020, key="ERRORS.GENERATION.CRM_NO_INJECTION")
    CRM_NO_CONSUMERS = Error(code=2021, key="ERRORS.GENERATION.CRM_NO_CONSUMERS")
    CRM_RANGE_TOO_LARGE = Error(code=2022, key="ERRORS.GENERATION.CRM_RANGE_TOO_LARGE")
    GET_CRM_PREVIEW = Error(code=2023, key="ERRORS.GENERATION.GET_CRM_PREVIEW")


class _Errors:
    auth = _AuthErrors()
    subscription = _SubscriptionErrors()
    generation = _GenerationErrors()


errors = _Errors()
