def build_footprint(polygon: list[list[float]]) -> str:
    """
    Converts a list of [lon, lat] pairs into a WKT POLYGON string suitable for
    OData spatial filter queries (OData.CSC.Intersects).
    Automatically closes the ring by appending the first point at the end if
    it is not already present, as required by the WKT spec.
    """
    coords    = polygon if polygon[0] == polygon[-1] else polygon + [polygon[0]]
    coord_str = ",".join(f"{lon} {lat}" for lon, lat in coords)
    return f"POLYGON(({coord_str}))"


def build_exclusion_filter(exclusion_zones: list[list[list[float]]]) -> str:
    """
    Builds an OData filter clause that excludes products intersecting any of the
    given polygons. Each zone is converted to a WKT footprint and negated using
    'not OData.CSC.Intersects(...)'. All clauses are joined with 'and', so a
    product is excluded if it overlaps even one zone.
    """
    clauses = []
    for zone in exclusion_zones:
        footprint = build_footprint(zone)
        clauses.append(f"not OData.CSC.Intersects(area=geography'SRID=4326;{footprint}')")
    return " and ".join(clauses)
