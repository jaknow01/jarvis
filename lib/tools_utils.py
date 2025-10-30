import json

def simplify_directions_response(data):
    routes_summary = []

    for route in data:
        for leg in route.get("legs", []):
            summary = {
                "start_address": leg.get("start_address"),
                "end_address": leg.get("end_address"),
                "departure_time": leg.get("departure_time", {}).get("text"),
                "arrival_time": leg.get("arrival_time", {}).get("text"),
                "total_distance": leg.get("distance", {}).get("text"),
                "total_duration": leg.get("duration", {}).get("text"),
                "steps": []
            }

            for step in leg.get("steps", []):
                step_info = {
                    "instruction": step.get("html_instructions"),
                    "distance": step.get("distance", {}).get("text"),
                    "duration": step.get("duration", {}).get("text"),
                    "travel_mode": step.get("travel_mode"),
                }

                if "transit_details" in step:
                    transit = step["transit_details"]
                    step_info["transit"] = {
                        "line": transit["line"].get("short_name") or transit["line"].get("name"),
                        "vehicle": transit["line"]["vehicle"].get("name"),
                        "departure_stop": transit["departure_stop"]["name"],
                        "arrival_stop": transit["arrival_stop"]["name"],
                        "num_stops": transit.get("num_stops")
                    }

                summary["steps"].append(step_info)

            routes_summary.append(summary)

    return routes_summary