"""Network configuration for TV channels and their column assignments.

Forked from cfb-grid-python/mern/python/v2/config/networks.py as of the
BigQuery-transform migration. Keep both copies in sync until/unless this
becomes a shared, single-sourced lookup.
"""

NETWORK_COLUMNS = {
    'ABC': 1,
    'CBS': 2,
    'FOX': 3,
    'NBC': 4,
    'ESPN': 5,
    'ESPN2': 6,
    'ESPNU': 7,
    'FS1': 8,
    'CBSSN': 9,
    'ACCN': 10,
    'BTN': 11,
    'PAC12': 12,
    'SECN': 13,
    'LHN': 14,
    'USA': 14,
    'NFL NET': 15,
    'CW NETWORK': 16,
    'TNT': 17,
    'ESPN+': 18,
    'Peacock': 18,
    'BIG12|ESPN+': 18,
    'HBCUGo': 18,
    'Flo': 18,
    'MWN': 18
}

NETWORK_DISPLAY_MAPPINGS = {
    'ACC Network': 'ACCN',
    'SEC Network': 'SECN',
    'The CW Network': 'CW NETWORK',
    'BIG12|ESPN+': 'ESPN+',
    'HBCU Go':'HBCUGo',
    'HBCU GO':'HBCUGo',
    'FloSports':'Flo',
    'MW Network':'MWN',
    'USA Net': 'USA',
    'USA NETWORK': 'USA'
}
