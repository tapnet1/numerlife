"""Country names, used to spot a country typed under "State / province" on the birth form.

Sovereign states (UN members and observers) by their common English names, plus frequent aliases
and native spellings. Names that are also the name of a state, province or region elsewhere
(Georgia; England, Scotland, Wales; Puerto Rico, Hong Kong, Macao, Taiwan) are deliberately left
out, so "Atlanta, Georgia, United States" or "Cardiff, Wales, United Kingdom" stay valid.
"""
from __future__ import annotations

import unicodedata

_NAMES = """
Afghanistan|Albania|Algeria|Andorra|Angola|Antigua and Barbuda|Argentina|Armenia|Australia|Austria|
Azerbaijan|Bahamas|The Bahamas|Bahrain|Bangladesh|Barbados|Belarus|Belgium|Belize|Benin|Bhutan|Bolivia|
Bosnia and Herzegovina|Bosnia|Botswana|Brazil|Brunei|Bulgaria|Burkina Faso|Burundi|Cabo Verde|Cape Verde|
Cambodia|Cameroon|Canada|Central African Republic|Chad|Chile|China|Colombia|Comoros|Congo|
Republic of the Congo|Democratic Republic of the Congo|DR Congo|DRC|Costa Rica|Cote d'Ivoire|
Ivory Coast|Croatia|Cuba|Cyprus|Czechia|Czech Republic|Denmark|Djibouti|Dominica|Dominican Republic|
Ecuador|Egypt|El Salvador|Equatorial Guinea|Eritrea|Estonia|Eswatini|Swaziland|Ethiopia|Fiji|Finland|
France|Gabon|Gambia|The Gambia|Germany|Ghana|Greece|Grenada|Guatemala|Guinea|Guinea-Bissau|Guyana|
Haiti|Honduras|Hungary|Iceland|India|Indonesia|Iran|Iraq|Ireland|Republic of Ireland|Israel|Italy|
Jamaica|Japan|Jordan|Kazakhstan|Kenya|Kiribati|Kosovo|Kuwait|Kyrgyzstan|Laos|Latvia|Lebanon|Lesotho|
Liberia|Libya|Liechtenstein|Lithuania|Luxembourg|Madagascar|Malawi|Malaysia|Maldives|Mali|Malta|
Marshall Islands|Mauritania|Mauritius|Mexico|Micronesia|Moldova|Monaco|Mongolia|Montenegro|Morocco|
Mozambique|Myanmar|Burma|Namibia|Nauru|Nepal|Netherlands|The Netherlands|Holland|New Zealand|Nicaragua|
Niger|Nigeria|North Korea|North Macedonia|Macedonia|Norway|Oman|Pakistan|Palau|Palestine|Panama|
Papua New Guinea|Paraguay|Peru|Philippines|Poland|Portugal|Qatar|Romania|Russia|Russian Federation|
Rwanda|Saint Kitts and Nevis|Saint Lucia|Saint Vincent and the Grenadines|Samoa|San Marino|
Sao Tome and Principe|Saudi Arabia|Senegal|Serbia|Seychelles|Sierra Leone|Singapore|Slovakia|Slovenia|
Solomon Islands|Somalia|South Africa|South Korea|Korea|Republic of Korea|South Sudan|Spain|Sri Lanka|
Sudan|Suriname|Sweden|Switzerland|Syria|Tajikistan|Tanzania|Thailand|Timor-Leste|East Timor|Togo|
Tonga|Trinidad and Tobago|Trinidad|Tunisia|Turkey|Turkiye|Turkmenistan|Tuvalu|Uganda|Ukraine|
United Arab Emirates|UAE|United Kingdom|UK|Great Britain|Britain|United States|United States of America|
USA|US|U.S.|U.S.A.|America|Uruguay|Uzbekistan|Vanuatu|Vatican City|Holy See|Venezuela|Vietnam|Viet Nam|
Yemen|Zambia|Zimbabwe|
Polska|Espana|Mexico|Peru|Brasil|Deutschland|Italia|Republica Dominicana|Rossiya|Nippon|Suomi|Sverige|
Norge|Danmark|Nederland|Belgique|Osterreich|Schweiz|Suisse|Magyarorszag|Ellada|Hrvatska|Srbija|
Slovensko|Cesko|Romania|Bulgariya|Ukraina|Turkiye|Misr|Philippines|Pilipinas
"""


def normalize(value: str) -> str:
    """Lower-case, accents removed, surrounding dots/spaces trimmed, inner spaces collapsed."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.replace("’", "'").lower().strip(" .").split())


COUNTRY_NAMES = frozenset(normalize(name) for name in _NAMES.replace("\n", "").split("|") if name.strip())


def is_country(value: str) -> bool:
    return normalize(value) in COUNTRY_NAMES
