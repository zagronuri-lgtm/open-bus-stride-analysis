<!-- RTL -->
# קטלוג Endpoints — Open Bus Stride API

> נוצר אוטומטית מ-OpenAPI spec. לכל endpoint מפורטים: שיטה, נתיב, תיאור, פרמטרים, ומקרה שימוש.


## אגרגציות

### `GET /gtfs_rides_agg/group_by`
**תיאור קצר:** Group By 
> gtfs rides aggregation grouped by given fields.
- **פרמטרים חובה:** `date_from, date_to, group_by`
- **פרמטרים אופציונליים:** `exclude_hours_from, exclude_hours_to`
- **מקרה שימוש לאנליסט:** נסיעות מתוכננות (GTFS)

### `GET /gtfs_rides_agg/list`
**תיאור קצר:** List 
> List of gtfs rides aggregations.
- **פרמטרים חובה:** `date_from, date_to`
- **פרמטרים אופציונליים:** `limit, offset, get_count, exclude_hours_from, exclude_hours_to`
- **מקרה שימוש לאנליסט:** נסיעות מתוכננות (GTFS)


## נתוני GTFS (תכנון)

### `GET /gtfs_agencies/list`
**תיאור קצר:** List 
> List of gtfs agencies.
- **פרמטרים אופציונליים:** `limit, offset, date_from, date_to`
- **מקרה שימוש לאנליסט:** רשימת מפעילים

### `GET /gtfs_ride_stops/get`
**תיאור קצר:** Get 
> Return a single gtfs ride stop based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** עצירות נסיעה מתוכננת

### `GET /gtfs_ride_stops/list`
**תיאור קצר:** List 
> List of gtfs ride stops.
- **פרמטרים אופציונליים:** `limit, offset, get_count, arrival_time_from, arrival_time_to, gtfs_stop_ids, gtfs_ride_ids, gtfs_ride__gtfs_route_id, gtfs_ride__journey_ref_prefix, gtfs_ride__start_time_from, gtfs_ride__start_time_to, gtfs_stop__date_from, gtfs_stop__date_to, gtfs_stop__code, gtfs_stop__city, gtfs_route__date_from, gtfs_route__date_to, gtfs_route__line_refs, gtfs_route__operator_refs, gtfs_route__route_short_name, gtfs_route__route_long_name_contains, gtfs_route__route_mkt, gtfs_route__route_direction, gtfs_route__route_alternative, gtfs_route__agency_name, gtfs_route__route_type, order_by`
- **מקרה שימוש לאנליסט:** עצירות נסיעה מתוכננת

### `GET /gtfs_rides/get`
**תיאור קצר:** Get 
> Return a single gtfs ride based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** נסיעות מתוכננות (GTFS)

### `GET /gtfs_rides/list`
**תיאור קצר:** List 
> List of gtfs rides.
- **פרמטרים אופציונליים:** `limit, offset, get_count, gtfs_route_id, journey_ref_prefix, start_time_from, start_time_to, gtfs_route__date_from, gtfs_route__date_to, gtfs_route__line_refs, gtfs_route__operator_refs, gtfs_route__route_short_name, gtfs_route__route_long_name_contains, gtfs_route__route_mkt, gtfs_route__route_direction, gtfs_route__route_alternative, gtfs_route__agency_name, gtfs_route__route_type, order_by`
- **מקרה שימוש לאנליסט:** נסיעות מתוכננות (GTFS)

### `GET /gtfs_routes/get`
**תיאור קצר:** Get 
> Return a single gtfs route based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** מסלולים מתוכננים (GTFS)

### `GET /gtfs_routes/list`
**תיאור קצר:** List 
> List of gtfs routes.
- **פרמטרים אופציונליים:** `limit, offset, get_count, date_from, date_to, line_refs, operator_refs, route_short_name, route_long_name_contains, route_mkt, route_direction, route_alternative, agency_name, route_type, order_by`
- **מקרה שימוש לאנליסט:** מסלולים מתוכננים (GTFS)

### `GET /gtfs_stops/get`
**תיאור קצר:** Get 
> Return a single gtfs stop based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** תחנות מתוכננות (GTFS)

### `GET /gtfs_stops/list`
**תיאור קצר:** List 
> List of gtfs stops.
- **פרמטרים אופציונליים:** `limit, offset, get_count, date_from, date_to, code, city`
- **מקרה שימוש לאנליסט:** תחנות מתוכננות (GTFS)


## נתוני SIRI (ביצוע)

### `GET /siri_ride_stops/get`
**תיאור קצר:** Get 
> Return a single siri ride stop based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** עצירות SIRI לנסיעה ספציפית

### `GET /siri_ride_stops/list`
**תיאור קצר:** List 
> List of siri ride stops.
- **פרמטרים אופציונליים:** `limit, offset, get_count, siri_stop_ids, siri_ride_ids, siri_vehicle_location__lon__greater_or_equal, siri_vehicle_location__lon__lower_or_equal, siri_vehicle_location__lat__greater_or_equal, siri_vehicle_location__lat__lower_or_equal, siri_vehicle_location__recorded_at_time_from, siri_vehicle_location__recorded_at_time_to, siri_ride__scheduled_start_time_from, siri_ride__scheduled_start_time_to, gtfs_stop__lat__greater_or_equal, gtfs_stop__lat__lower_or_equal, gtfs_stop__lon__greater_or_equal, gtfs_stop__lon__lower_or_equal, gtfs_date_from, gtfs_date_to, order_by`
- **מקרה שימוש לאנליסט:** עצירות SIRI לנסיעה ספציפית

### `GET /siri_rides/get`
**תיאור קצר:** Get 
> Return a single siri ride based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** נסיעות SIRI עם נתוני ביצוע

### `GET /siri_rides/list`
**תיאור קצר:** List 
> List of siri rides.
- **פרמטרים אופציונליים:** `limit, offset, get_count, gtfs_route__date_from, gtfs_route__date_to, gtfs_route__line_refs, gtfs_route__operator_refs, gtfs_route__route_short_name, gtfs_route__route_long_name_contains, gtfs_route__route_mkt, gtfs_route__route_direction, gtfs_route__route_alternative, gtfs_route__agency_name, gtfs_route__route_type, gtfs_ride__gtfs_route_id, gtfs_ride__journey_ref_prefix, gtfs_ride__start_time_from, gtfs_ride__start_time_to, siri_route_ids, siri_route__line_refs, siri_route__operator_refs, journey_ref_prefix, journey_refs, vehicle_refs, scheduled_start_time_from, scheduled_start_time_to, order_by`
- **מקרה שימוש לאנליסט:** נסיעות SIRI עם נתוני ביצוע

### `GET /siri_routes/get`
**תיאור קצר:** Get 
> Return a single siri route based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** מסלולי SIRI (זיהוי קו בזמן אמת)

### `GET /siri_routes/list`
**תיאור קצר:** List 
> List of siri routes.
- **פרמטרים אופציונליים:** `limit, offset, get_count, line_refs, operator_refs, order_by`
- **מקרה שימוש לאנליסט:** מסלולי SIRI (זיהוי קו בזמן אמת)

### `GET /siri_snapshots/get`
**תיאור קצר:** Get 
> Return a single siri snapshot based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** סטטוס תמונות ETL

### `GET /siri_snapshots/list`
**תיאור קצר:** List 
> List of siri snapshots.
- **פרמטרים אופציונליים:** `limit, offset, get_count, snapshot_id_prefix, order_by`
- **מקרה שימוש לאנליסט:** סטטוס תמונות ETL

### `GET /siri_stops/get`
**תיאור קצר:** Get 
> Return a single siri stop based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** תחנות SIRI

### `GET /siri_stops/list`
**תיאור קצר:** List 
> List of siri stops.
- **פרמטרים אופציונליים:** `limit, offset, get_count, codes, order_by`
- **מקרה שימוש לאנליסט:** תחנות SIRI

### `GET /siri_vehicle_locations/get`
**תיאור קצר:** Get 
> Return a single siri vehicle location based on id
- **פרמטרים חובה:** `id`
- **מקרה שימוש לאנליסט:** מיקומי רכב מ-SIRI

### `GET /siri_vehicle_locations/list`
**תיאור קצר:** List 
> List of siri vehicle locations.
- **פרמטרים אופציונליים:** `limit, offset, get_count, siri_vehicle_location_ids, siri_snapshot_ids, siri_ride_stop_ids, recorded_at_time_from, recorded_at_time_to, lon__greater_or_equal, lon__lower_or_equal, lat__greater_or_equal, lat__lower_or_equal, order_by, siri_routes__line_ref, siri_ride__vehicle_ref, siri_routes__operator_ref, siri_rides__schedualed_start_time_from, siri_rides__schedualed_start_time_to, siri_rides__ids, siri_routes__ids`
- **מקרה שימוש לאנליסט:** מיקומי רכב מ-SIRI

### `GET /siri_velocity_aggregation/siri_velocity_aggregation`
**תיאור קצר:** Siri Velocity Aggregation
- **פרמטרים חובה:** `recorded_from`
- **פרמטרים אופציונליים:** `lon_min, lon_max, lat_min, lat_max, rounding_precision`
- **מקרה שימוש לאנליסט:** מהירות ממוצעת לרכב/קו


## מקרי שימוש

### `GET /rides_execution/list`
**תיאור קצר:** List 
> List of A comparison between the planned and actual rides of a specific route between the given dates.
- **פרמטרים חובה:** `date_from, date_to, operator_ref, line_ref`
- **פרמטרים אופציונליים:** `limit, offset, get_count`
- **מקרה שימוש לאנליסט:** השוואת נסיעות מתוכננות מול ביצוע בפועל

### `GET /route_timetable/list`
**תיאור קצר:** List 
> List of the stops timetable of a given bus.
- **פרמטרים אופציונליים:** `limit, offset, get_count, planned_start_time_date_from, planned_start_time_date_to, line_refs`
- **מקרה שימוש לאנליסט:** לוח זמנים מתוכנן לתחנה/קו לתאריך נתון

### `GET /stop_arrivals/list`
**תיאור קצר:** List 
> List of the actual arrival times to a specific stop.
- **פרמטרים אופציונליים:** `limit, offset, get_count, gtfs_stop_id, gtfs_ride_ids`
- **מקרה שימוש לאנליסט:** זמני הגעה בפועל לתחנה (SIRI)
