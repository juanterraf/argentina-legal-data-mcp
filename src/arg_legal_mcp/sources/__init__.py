"""Additional Argentine public-data sources (beyond InfoLEG).

Implemented: dólar (cotizaciones), feriados nacionales. The same small pattern
(`base.get_json` + a thin tool) extends to BCRA, INDEC, AFIP, legislación tributaria
and Boletín Oficial — see TODOs in DECISIONS.md / STATUS.md.
"""
