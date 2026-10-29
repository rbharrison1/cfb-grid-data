"""College basketball network -> grid column configuration.

Built from a full inventory of every broadcastName CBBD's /games/media
returned for the 2025-26 season plus Nov 2026 (92 distinct names across
~7,900 games). Column numbers deliberately reuse the football grid's
numbering (transform/config/networks.py) wherever the network is the same,
so cfb-grid-web can share one column/logo table between sports. 12 is the
BTN overflow column, 18 is the streaming section.

Unlike football, an outlet with no column is NOT dropped: anything that
isn't a national linear network (ESPN+, conference streaming platforms,
regional sports networks, local stations) is placed in the streaming
section, so a game is only ever missing from the grid if CBBD has no
broadcast for it at all (or only a radio one).
"""

STREAMING_COL = 18
BTN_OVERFLOW_COL = 12

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
    'SECN': 13,
    'USA': 14,
    'TBS': 15,
    'CW': 16,
    'TNT': 17,
    'truTV': 19,
    'ESPNEWS': 20,
    'FS2': 21,
    'NBCSN': 22,
}

# CBBD spelling -> the outlet name stored in Mongo (and shown on the grid).
NETWORK_DISPLAY_MAPPINGS = {
    'ACC Network': 'ACCN',
    'SEC Network': 'SECN',
    'USA Net': 'USA',
    'The CW Network': 'CW',
    'Fox': 'FOX',
    'FloSports': 'Flo',
    'ACC Extra': 'ACCNX',
    'Midco Sports Plus': 'MidCo+',
    'Midco Sports Plus 2': 'MidCo+',
    'ESPN Unlmtd': 'ESPN Unlimited',
    'HBCU Go': 'HBCUGo',
    'Summit League Network': 'Summit League Net',
}

# broadcastType values that never make a game placeable (CBBD lists some
# radio-only broadcasts, e.g. 'ERADM').
IGNORED_BROADCAST_TYPES = {'Radio'}

# Outlets seen in the inventory that intentionally land in the streaming
# section. Anything NOT in this set or NETWORK_COLUMNS still goes to the
# streaming section too, but is reported as 'unmapped' in the transform
# response so a new national network doesn't slip in unnoticed.
KNOWN_STREAMING_OUTLETS = {
    'ESPN+', 'ESPN Unlimited', 'Peacock', 'Paramount+', 'HBO Max', 'YouTube',
    'Flo', 'SECN+', 'ACCNX', 'B1G+', 'MWN', 'MW+', 'NEC Front Row',
    'Summit League Net', 'MidCo+', 'Patriot League TV', 'BallerTV', 'PTB Live',
    'DSN', 'SWAC TV', 'HBCUGo', 'Cowboy Sports Network', 'WDAY Xtra',
    'NESN+', 'Rock Entertainment Sports Network', 'Fox 9+', 'Scripps Sports',
    'SWX (Montana)', 'UNLVRebels.com', 'SJSU Athletics', 'GoLobos.com',
    # Regional sports networks and local broadcast stations.
    'Spectrum Sports Net', 'MVC', 'MASN', 'SNY', 'MNMT', 'NESN', 'WDAY',
    'Midco Sports Net', 'NBC Sports Phil', 'NBC Sports Phil +',
    'Marquee Sports Net', 'FanDuel SN OH', 'WITN-TV', 'MC22', 'WNDY-TV',
    'Altitude Sports', 'Altitude 2 Sports', 'MSG', 'MSG2', 'MSGSN', 'WHIO-TV',
    'SWX Local Sports', 'YES', 'WRAL-TV', 'Urban Edge Network', 'Fox 9 MN',
    'My Utah', 'Cape Fear CW', 'CBS6', 'SportsNet PIT', 'SportsNet PIT+',
    'WCBD-TV', 'WSMV', 'KMAX 31', 'MeTV Baltimore', 'CNBC',
}
