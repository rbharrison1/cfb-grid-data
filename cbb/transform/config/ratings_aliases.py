"""Ratings-source team name -> CBBD `school`, for names ratings_match.py's
normalization can't resolve on its own (it already handles case, punctuation,
accents, "State"/"St.", "Saint"/"St.", and CBBD's shortDisplayName and
abbreviation).

Hand-maintained, basketball-only. Deliberately separate from team_data's
teams.json, whose aliases are football-oriented (e.g. 'Little Rock' is an
alias of Arkansas Baptist there). When the transform reports
`ratings_unmatched`, add the missing name here. Values must be exact CBBD
`school` strings; test_ratings.py checks every one exists.

Seeded 2026-11-24 from the 2026-27 Torvik CSV and the 2025-26 final NET.
"""

TORVIK_ALIASES = {
    "Albany": "UAlbany",
    "Appalachian St.": "App State",
    "Cal Baptist": "California Baptist",
    "Connecticut": "UConn",
    "Grambling St.": "Grambling",
    "Illinois Chicago": "UIC",
    "Louisiana Monroe": "UL Monroe",
    "McNeese St.": "McNeese",
    "Miami FL": "Miami",
    "Mississippi": "Ole Miss",
    "N.C. State": "NC State",
    "Nebraska Omaha": "Omaha",
    "Nicholls St.": "Nicholls",
    "Sam Houston St.": "Sam Houston",
    "Seattle": "Seattle U",
    "Southeastern Louisiana": "SE Louisiana",
    "St. Thomas": "St. Thomas-Minnesota",
    "Tennessee Martin": "UT Martin",
    "Texas A&M Corpus Chris": "Texas A&M-Corpus Christi",
    "UMKC": "Kansas City",
    "USC Upstate": "South Carolina Upstate",
}

# NCAA.com uses AP-style abbreviations ("Fla.", "Ky.", "Caro.").
NET_ALIASES = {
    "A&M-Corpus Christi": "Texas A&M-Corpus Christi",
    "Alcorn": "Alcorn State",
    "Ark.-Pine Bluff": "Arkansas-Pine Bluff",
    "Army West Point": "Army",
    "CSU Bakersfield": "Cal State Bakersfield",
    "Central Ark.": "Central Arkansas",
    "Central Conn. St.": "Central Connecticut",
    "Central Mich.": "Central Michigan",
    "Col. of Charleston": "Charleston",
    "Eastern Ill.": "Eastern Illinois",
    "Eastern Ky.": "Eastern Kentucky",
    "Eastern Mich.": "Eastern Michigan",
    "Eastern Wash.": "Eastern Washington",
    "Fla. Atlantic": "Florida Atlantic",
    "LMU (CA)": "Loyola Marymount",
    "Lamar University": "Lamar",
    "Miami (FL)": "Miami",
    "Middle Tenn.": "Middle Tennessee",
    "Mississippi Val.": "Mississippi Valley State",
    "N.C. A&T": "North Carolina A&T",
    "N.C. Central": "North Carolina Central",
    "North Ala.": "North Alabama",
    "Northern Ariz.": "Northern Arizona",
    "Northern Colo.": "Northern Colorado",
    "Northern Ky.": "Northern Kentucky",
    "Queens (NC)": "Queens University",
    "Saint Mary's (CA)": "Saint Mary's",
    "South Fla.": "South Florida",
    "Southeast Mo. St.": "Southeast Missouri State",
    "Southeastern La.": "SE Louisiana",
    "Southern California": "USC",
    "Southern Ill.": "Southern Illinois",
    "Southern Ind.": "Southern Indiana",
    "Southern U.": "Southern",
    "St. John's (NY)": "St. John's",
    "USC Upstate": "South Carolina Upstate",
    "UTRGV": "UT Rio Grande Valley",
    "West Ga.": "West Georgia",
    "Western Caro.": "Western Carolina",
    "Western Ill.": "Western Illinois",
    "Western Mich.": "Western Michigan",
}
