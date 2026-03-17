"""Study Detector Lambda — scans DynamoDB weather items from the last 10 days,
detects persistent/cluster weather events, and creates suggested study items."""

import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "art-generator")


def scan_all(table):
    """Paginated DynamoDB scan — returns all items."""
    items = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return items


def group_by_grid(items, cell_size=5):
    """Groups weather items by lat/lng grid cells.

    Cell key = (int(lat // cell_size) * cell_size, int(lng // cell_size) * cell_size).
    Returns dict mapping cell keys to lists of items.
    """
    grid = defaultdict(list)
    for item in items:
        lat = float(item.get("lat", 0))
        lng = float(item.get("lng", 0))
        cell_lat = int(lat // cell_size) * cell_size
        cell_lng = int(lng // cell_size) * cell_size
        grid[(cell_lat, cell_lng)].append(item)
    return dict(grid)


def detect_persistent(grid, min_days=3):
    """Finds grid cells with items on 3+ consecutive days.

    Returns list of dicts: {center_lat, center_lng, dates, items}.
    """
    results = []
    for (cell_lat, cell_lng), items in grid.items():
        # Collect unique dates
        dates = set()
        for item in items:
            sk = item.get("SK", "")
            if "#" in sk:
                date_str = sk.split("#")[-1]
                try:
                    dates.add(date_str)
                except (ValueError, IndexError):
                    continue

        if len(dates) < min_days:
            continue

        sorted_dates = sorted(dates)
        # Find consecutive runs
        best_run = []
        current_run = [sorted_dates[0]]

        for i in range(1, len(sorted_dates)):
            try:
                prev = datetime.strptime(current_run[-1], "%Y-%m-%d")
                curr = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
                if (curr - prev).days == 1:
                    current_run.append(sorted_dates[i])
                else:
                    if len(current_run) > len(best_run):
                        best_run = current_run
                    current_run = [sorted_dates[i]]
            except ValueError:
                current_run = [sorted_dates[i]]

        if len(current_run) > len(best_run):
            best_run = current_run

        if len(best_run) >= min_days:
            results.append({
                "center_lat": cell_lat + 2.5,
                "center_lng": cell_lng + 2.5,
                "dates": best_run,
                "items": items,
            })

    return results


def detect_clusters(grid, min_points=3, max_distance=15):
    """Finds days where 3+ grid cells within max_distance degrees are all active.

    Returns list of dicts: {date, coordinates}.
    """
    # Build date -> list of active cell centers
    date_cells = defaultdict(list)
    for (cell_lat, cell_lng), items in grid.items():
        center = (cell_lat + 2.5, cell_lng + 2.5)
        for item in items:
            sk = item.get("SK", "")
            if "#" in sk:
                date_str = sk.split("#")[-1]
                date_cells[date_str].append(center)

    # Deduplicate centers per date
    for date_str in date_cells:
        date_cells[date_str] = list(set(date_cells[date_str]))

    results = []
    for date_str, centers in date_cells.items():
        if len(centers) < min_points:
            continue

        # Check if any subset of min_points centers are all within max_distance
        # For each center, find neighbors within max_distance
        for i, c1 in enumerate(centers):
            nearby = [c1]
            for j, c2 in enumerate(centers):
                if i == j:
                    continue
                dist = math.sqrt((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2)
                if dist <= max_distance:
                    nearby.append(c2)

            if len(nearby) >= min_points:
                results.append({
                    "date": date_str,
                    "coordinates": nearby,
                })
                break  # One cluster per date is enough

    return results


def is_duplicate_study(existing_studies, lat, lng, start_date, end_date):
    """Checks if a proposed study overlaps an existing one.

    Overlap = region within 10 degrees AND date ranges overlap.
    """
    for study in existing_studies:
        s_lat = float(study.get("lat", 0))
        s_lng = float(study.get("lng", 0))
        s_start = study.get("start_date", "")
        s_end = study.get("end_date", "")

        # Region overlap: both lat and lng within 10 degrees
        if abs(s_lat - lat) <= 10 and abs(s_lng - lng) <= 10:
            # Date overlap check
            if s_start <= end_date and s_end >= start_date:
                return True

    return False


def handler(event, context):
    """Scans DynamoDB for recent weather items, detects patterns,
    creates suggested study items, and updates active studies."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)

    # Get all items
    all_items = scan_all(table)

    # Filter to WEATHER# items from last 10 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    weather_items = []
    existing_studies = []

    for item in all_items:
        pk = item.get("PK", "")
        sk = item.get("SK", "")
        if pk.startswith("WEATHER#"):
            if "#" in sk:
                date_str = sk.split("#")[-1]
                if date_str >= cutoff:
                    weather_items.append(item)
        elif pk.startswith("STUDY#") and sk == "META":
            existing_studies.append(item)

    # Group weather items by grid
    grid = group_by_grid(weather_items)

    # Detect patterns
    persistent = detect_persistent(grid)
    clusters = detect_clusters(grid)

    studies_created = 0

    # Create study suggestions from persistent events
    for event_data in persistent:
        lat = event_data["center_lat"]
        lng = event_data["center_lng"]
        start_date = event_data["dates"][0]
        end_date = event_data["dates"][-1]

        if is_duplicate_study(existing_studies, lat, lng, start_date, end_date):
            continue

        study_id = f"auto-persistent-{int(lat)}-{int(lng)}-{start_date}"
        table.put_item(Item={
            "PK": f"STUDY#{study_id}",
            "SK": "META",
            "name": f"Persistent weather at {lat:.1f},{lng:.1f}",
            "status": "suggested",
            "lat": Decimal(str(lat)),
            "lng": Decimal(str(lng)),
            "start_date": start_date,
            "end_date": end_date,
            "detection_type": "persistent",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        studies_created += 1

    # Create study suggestions from cluster events
    for cluster in clusters:
        coords = cluster["coordinates"]
        avg_lat = sum(c[0] for c in coords) / len(coords)
        avg_lng = sum(c[1] for c in coords) / len(coords)
        date = cluster["date"]

        if is_duplicate_study(existing_studies, avg_lat, avg_lng, date, date):
            continue

        study_id = f"auto-cluster-{int(avg_lat)}-{int(avg_lng)}-{date}"
        table.put_item(Item={
            "PK": f"STUDY#{study_id}",
            "SK": "META",
            "name": f"Weather cluster near {avg_lat:.1f},{avg_lng:.1f}",
            "status": "suggested",
            "lat": Decimal(str(avg_lat)),
            "lng": Decimal(str(avg_lng)),
            "start_date": date,
            "end_date": date,
            "detection_type": "cluster",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        studies_created += 1

    # Update active studies with matching artworks
    active_studies = [s for s in existing_studies if s.get("status") == "active"]
    artworks_matched = 0
    for study in active_studies:
        s_lat = float(study.get("lat", 0))
        s_lng = float(study.get("lng", 0))
        s_start = study.get("start_date", "")
        s_end = study.get("end_date", "")
        study_pk = study["PK"]

        matching = []
        for item in weather_items:
            w_lat = float(item.get("lat", 0))
            w_lng = float(item.get("lng", 0))
            sk = item.get("SK", "")
            if "#" in sk:
                date_str = sk.split("#")[-1]
                if (abs(w_lat - s_lat) <= 10 and abs(w_lng - s_lng) <= 10
                        and s_start <= date_str <= s_end):
                    matching.append(item.get("PK", ""))

        if matching:
            table.update_item(
                Key={"PK": study_pk, "SK": "META"},
                UpdateExpression="SET matching_artworks = :m",
                ExpressionAttributeValues={":m": matching},
            )
            artworks_matched += len(matching)

    return {
        "statusCode": 200,
        "body": {
            "weather_items_scanned": len(weather_items),
            "persistent_events": len(persistent),
            "cluster_events": len(clusters),
            "studies_created": studies_created,
            "artworks_matched": artworks_matched,
        },
    }
