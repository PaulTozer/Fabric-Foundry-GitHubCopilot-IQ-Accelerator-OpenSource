"""Generate scaled, linked Example Enterprise consultancy demo data.

Writes JSON files under ./_demo_data_files for the 12 ENT_* tables defined in
enterprise_schema.py.
"""
from __future__ import annotations

import datetime as dt
import json
from random import Random

TODAY = dt.date(2026, 7, 7)

FIRST = ["Amelia", "James", "Priya", "Owen", "Charlotte", "Ravi", "Sofia", "Thomas", "Megan", "Daniel", "Leila", "Oliver", "Aisha", "George", "Lucy", "Arjun", "Isla", "Ben"]
LAST = ["Patel", "Smith", "Davies", "Brown", "Taylor", "Wilson", "Thomas", "Evans", "Roberts", "Green", "Walker", "Jackson", "Scott", "Lewis", "Ali", "Baker"]

OFFICES = [
    ("LON", "London", "UK", "UK & Europe"),
    ("BHM", "Birmingham", "UK", "UK & Europe"),
    ("MAN", "Manchester", "UK", "UK & Europe"),
    ("GLA", "Glasgow", "UK", "UK & Europe"),
    ("ADB", "Abu Dhabi", "UAE", "Middle East"),
    ("DEL", "Delhi", "India", "South Asia"),
    ("SYD", "Sydney", "Australia", "APAC"),
    ("TOR", "Toronto", "Canada", "North America"),
]
PRACTICES = ["Transport", "Energy", "Water", "Buildings", "Environment", "Programme Management", "Advisory", "Digital"]
DISCIPLINES = ["Civil", "Structural", "Rail Systems", "Highways", "Water Engineering", "Power Systems", "Sustainability", "Project Controls", "Digital Delivery", "Cost Advisory"]
GRADES = ["Consultant", "Senior Consultant", "Principal", "Associate", "Technical Director", "Unit General Manager"]
STATUSES = ["Active"] * 18 + ["Secondment", "Leave"]
SECTORS = ["Transport", "Energy", "Water", "Buildings", "Environment and Society", "Defence and Security"]
MARKETS = ["Rail", "Highways", "Aviation", "Ports", "Energy Networks", "Hydrogen", "Water Utilities", "Property and Buildings", "Resilience", "Defence"]
FRAMEWORKS = ["Example Highways", "Example Rail", "Example Environment", "Example Grid", "Example Transport Framework", "Major Projects Advisory", "Example Water", "Local Authority Partnerships"]
CONTRACT_MODELS = ["Time and Materials", "Lump Sum", "Target Cost", "Framework Call-Off", "NEC Professional Services"]
CAPABILITIES = [
    ("DIG-TWIN", "Digital Twin Delivery", "Transport", "Digital", "Digital Delivery"),
    ("BIM-ISO", "ISO 19650 BIM Delivery", "Buildings", "Digital", "Digital Delivery"),
    ("CARB-ADV", "Carbon Advisory", "Energy", "Sustainability", "Net Zero"),
    ("FLOOD-R", "Flood Resilience", "Water", "Water", "Climate Adaptation"),
    ("RAIL-SYS", "Rail Systems Integration", "Transport", "Rail", "Systems Delivery"),
    ("AIR-MAST", "Aviation Masterplanning", "Transport", "Aviation", "Growth"),
    ("GRID-TRANS", "Grid Transformation", "Energy", "Power", "Energy Transition"),
    ("PMO-MJR", "Major Project PMO", "Transport", "Programme Management", "Delivery Excellence"),
    ("NATURE-P", "Nature Positive Design", "Environment", "Ecology", "Biodiversity"),
    ("TUNN-ENG", "Tunnelling Engineering", "Transport", "Civil", "Complex Infrastructure"),
]
SKILLS = [
    ("SK-ISO19650", "ISO 19650 Delivery", "Digital", "BIM-ISO", True),
    ("SK-P6", "Primavera P6", "Project Controls", "PMO-MJR", False),
    ("SK-RAILINT", "Rail Interface Management", "Transport", "RAIL-SYS", False),
    ("SK-CARBON", "Whole-Life Carbon Modelling", "Sustainability", "CARB-ADV", False),
    ("SK-FLOOD", "Flood Risk Modelling", "Water", "FLOOD-R", False),
    ("SK-PLATFORM", "Digital Platform Configuration", "Digital", "DIG-TWIN", True),
    ("SK-GIS", "Spatial Analytics", "Digital", "DIG-TWIN", True),
    ("SK-AIRPORT", "Airport Planning", "Aviation", "AIR-MAST", False),
    ("SK-TUNNEL", "Tunnel Design Coordination", "Civil", "TUNN-ENG", False),
    ("SK-GRID", "Grid Connection Studies", "Energy", "GRID-TRANS", False),
]
NETWORKS = [
    "Delivering Excellence",
    "Digital Delivery Network",
    "Future Ready Water",
    "Nature Positive Collective",
    "Carbon Advisory Forum",
    "Rail Systems Community",
    "Programme Leadership Circle",
    "Digital Platform Champions",
]
CLIENT_NAMES = [
    "Example Grid", "Example Rail", "Example Infrastructure", "Example City Transport",
    "Example Environment", "Example Water North", "Example Utilities", "Example Water South",
    "Example Highways", "Example Airport", "Example Estates", "Example Water West",
    "Example Metro", "Example Transit", "Example Line Delivery", "Example Mobility"
]
ROLE_NAMES = ["Project Manager", "Design Lead", "Technical Assurance", "Systems Lead", "Commercial Lead", "Sustainability Lead", "Digital Delivery Lead", "Project Controls"]
PROF_LEVELS = ["Foundation", "Practitioner", "Advanced", "Expert"]


def generate_data(*, small: bool = False) -> dict[str, list[dict]]:
    """Build deterministic synthetic rows without files or global RNG changes."""
    random = Random(20260707)
    N_STAFF, N_CLIENTS, N_PROGRAMMES, N_PROJECTS = (
        (24, 16, 8, 48) if small else (900, 650, 220, 2600)
    )
    def pick_name() -> str:
        return f"{random.choice(FIRST)} {random.choice(LAST)}"


    def daterange(years_back: int) -> dt.date:
        return TODAY - dt.timedelta(days=random.randint(30, years_back * 365))


    staff = []
    for staff_id in range(1, N_STAFF + 1):
        office = OFFICES[(staff_id - 1) % len(OFFICES)]
        practice = random.choice(PRACTICES)
        discipline = random.choice(DISCIPLINES)
        grade = random.choices(GRADES, weights=[26, 28, 20, 14, 8, 4], k=1)[0]
        name = pick_name()
        upn = name.lower().replace(" ", ".") + f"{staff_id}@enterprise.example"
        manager_id = None if staff_id <= 20 else random.randint(max(1, staff_id - 45), staff_id - 1)
        manager_name = "" if manager_id is None else f"Manager {manager_id}"
        staff.append({
            "StaffID": staff_id,
            "StaffUPN": upn,
            "StaffName": name,
            "StaffStatus": random.choice(STATUSES),
            "Email": upn,
            "OfficeCode": office[0],
            "OfficeName": office[1],
            "CountryName": office[2],
            "RegionName": office[3],
            "PracticeName": practice,
            "DisciplineName": discipline,
            "Grade": grade,
            "JobTitle": f"{discipline} {grade}",
            "StartDate": daterange(12).isoformat(),
            "LeaveDate": None,
            "ManagerID": manager_id,
            "ManagerName": manager_name,
            "ManagerEmail": "" if manager_id is None else f"manager.{manager_id}@enterprise.example",
            "ReportsToUPN": "" if manager_id is None else f"manager.{manager_id}@enterprise.example",
            "MyPeoplePageURL": f"https://people.enterprise.example/staff/{staff_id}",
        })

    for row in staff:
        if row["ManagerID"]:
            mgr = staff[row["ManagerID"] - 1]
            row["ManagerName"] = mgr["StaffName"]
            row["ManagerEmail"] = mgr["Email"]
            row["ReportsToUPN"] = mgr["StaffUPN"]

    capabilities = []
    for idx, (code, name, market, tech, theme) in enumerate(CAPABILITIES, start=1):
        capabilities.append({
            "CapabilityID": idx,
            "CapabilityCode": code,
            "CapabilityName": name,
            "MarketName": market,
            "TechnicalArea": tech,
            "StrategicTheme": theme,
            "Billable": True,
        })

    skills = []
    for idx, (code, name, category, related_capability, digital) in enumerate(SKILLS, start=1):
        skills.append({
            "SkillID": idx,
            "SkillCode": code,
            "SkillName": name,
            "SkillCategory": category,
            "RelatedCapabilityCode": related_capability,
            "DigitalSkill": digital,
        })

    staff_caps = []
    for row in staff:
        for cap in random.sample(capabilities, k=random.randint(2, 4)):
            staff_caps.append({
                "StaffID": row["StaffID"],
                "StaffName": row["StaffName"],
                "StaffUPN": row["StaffUPN"],
                "CapabilityCode": cap["CapabilityCode"],
                "CapabilityName": cap["CapabilityName"],
                "ProficiencyLevel": random.choices(PROF_LEVELS, weights=[2, 4, 3, 1], k=1)[0],
                "YearsExperience": random.randint(2, 18),
                "Accredited": random.random() < 0.45,
                "PrimaryCapability": False,
            })
        own = [x for x in staff_caps if x["StaffID"] == row["StaffID"]]
        random.choice(own)["PrimaryCapability"] = True

    staff_skills = []
    skills_by_cap = {}
    for item in skills:
        skills_by_cap.setdefault(item["RelatedCapabilityCode"], []).append(item)
    for row in staff:
        cap_codes = [x["CapabilityCode"] for x in staff_caps if x["StaffID"] == row["StaffID"]]
        pool = [s for code in cap_codes for s in skills_by_cap.get(code, [])]
        if not pool:
            pool = skills
        chosen = random.sample(pool, k=min(len(pool), random.randint(2, 4)))
        for item in chosen:
            staff_skills.append({
                "StaffID": row["StaffID"],
                "StaffName": row["StaffName"],
                "StaffUPN": row["StaffUPN"],
                "SkillCode": item["SkillCode"],
                "SkillName": item["SkillName"],
                "ProficiencyLevel": random.choices(PROF_LEVELS, weights=[2, 4, 3, 1], k=1)[0],
                "YearsExperience": random.randint(1, 15),
                "PrimarySkill": False,
            })
        own = [x for x in staff_skills if x["StaffID"] == row["StaffID"]]
        random.choice(own)["PrimarySkill"] = True

    network_members = []
    for net_id, net_name in enumerate(NETWORKS, start=1):
        for member in random.sample(staff, k=min(len(staff), random.randint(60, 180))):
            network_members.append({
                "PracticeNetworkID": net_id,
                "PracticeNetworkName": net_name,
                "StaffID": member["StaffID"],
                "StaffEmail": member["Email"],
                "StaffUPN": member["StaffUPN"],
                "StaffName": member["StaffName"],
                "Current": True,
            })

    clients = []
    for client_id in range(1, N_CLIENTS + 1):
        seed_name = CLIENT_NAMES[(client_id - 1) % len(CLIENT_NAMES)]
        director = random.choice(staff)
        clients.append({
            "ClientID": client_id,
            "ClientCode": f"CLI-{client_id:05d}",
            "ClientName": seed_name if client_id <= len(CLIENT_NAMES) else f"{seed_name} {client_id}",
            "ClientStatusDesc": random.choices(["Active", "Prospect", "Dormant"], weights=[16, 3, 1], k=1)[0],
            "ClientSectorName": random.choice(SECTORS),
            "CountryName": random.choice(["UK", "Australia", "Canada", "India", "UAE"]),
            "RelationshipDirectorID": director["StaffID"],
            "RelationshipDirectorName": director["StaffName"],
            "RelationshipDirectorEmail": director["Email"],
            "StrategicClient": random.random() < 0.18,
            "FrameworkClient": random.random() < 0.34,
            "ActiveProjectCount": 0,
            "TotalBacklogValue": 0.0,
        })

    programmes = []
    for programme_id in range(1, N_PROGRAMMES + 1):
        client = random.choice(clients)
        director = random.choice(staff)
        programmes.append({
            "ProgrammeID": programme_id,
            "ProgrammeCode": f"PRG-{programme_id:04d}",
            "ProgrammeName": f"{random.choice(MARKETS)} Transformation {programme_id}",
            "ClientID": client["ClientID"],
            "ClientName": client["ClientName"],
            "MarketName": random.choice(MARKETS),
            "RegionName": random.choice(["UK & Europe", "Middle East", "APAC", "North America"]),
            "StartDate": daterange(6).isoformat(),
            "EndDate": (TODAY + dt.timedelta(days=random.randint(120, 1200))).isoformat(),
            "ProgrammeDirectorID": director["StaffID"],
            "ProgrammeDirectorName": director["StaffName"],
            "Status": random.choices(["Active", "Mobilising", "Closing"], weights=[15, 3, 1], k=1)[0],
            "TotalProgrammeValue": float(random.randint(20, 220)) * 1_000_000,
        })

    projects = []
    for project_id in range(1, N_PROJECTS + 1):
        client = random.choice(clients)
        programme = random.choice(programmes) if random.random() < 0.75 else None
        director = random.choice(staff)
        manager = random.choice(staff)
        commercial = random.choice(staff)
        sector = client["ClientSectorName"]
        total_fee = float(random.randint(2, 65)) * 1_000_000
        pct = round(random.uniform(5, 96), 2)
        projects.append({
            "ProjectID": project_id,
            "ProjectCode": f"PROJ-{project_id:05d}",
            "ShortTitle": f"{random.choice(MARKETS)} Delivery {project_id}",
            "FullTitle": f"{client['ClientName']} {random.choice(MARKETS)} Delivery Commission {project_id}",
            "ClientID": client["ClientID"],
            "ClientName": client["ClientName"],
            "ProgrammeID": None if programme is None else programme["ProgrammeID"],
            "ProgrammeName": "" if programme is None else programme["ProgrammeName"],
            "MarketName": random.choice(MARKETS),
            "SectorName": sector,
            "CountryName": client["CountryName"],
            "RegionName": random.choice(["UK & Europe", "Middle East", "APAC", "North America"]),
            "ProjectStatusDesc": random.choices(["Active", "Mobilising", "On Hold", "Closing"], weights=[15, 2, 1, 2], k=1)[0],
            "StartDate": daterange(5).isoformat(),
            "EndDate": (TODAY + dt.timedelta(days=random.randint(90, 900))).isoformat(),
            "ProjectDirectorID": director["StaffID"],
            "ProjectDirectorName": director["StaffName"],
            "ProjectManagerID": manager["StaffID"],
            "ProjectManagerName": manager["StaffName"],
            "CommercialManagerID": commercial["StaffID"],
            "CommercialManagerName": commercial["StaffName"],
            "FrameworkName": random.choice(FRAMEWORKS) if client["FrameworkClient"] else "",
            "ContractModel": random.choice(CONTRACT_MODELS),
            "TotalFee": total_fee,
            "PercentComplete": pct,
            "BIMEnabled": random.random() < 0.54,
            "DigitalTwinEnabled": random.random() < 0.22,
        })

    for project in projects:
        client = clients[project["ClientID"] - 1]
        if project["ProjectStatusDesc"] == "Active":
            client["ActiveProjectCount"] += 1
        client["TotalBacklogValue"] += project["TotalFee"] * (1 - (project["PercentComplete"] / 100.0))

    project_staff = []
    for project in projects:
        team_size = random.randint(5, 12)
        for member in random.sample(staff, k=team_size):
            project_staff.append({
                "ProjectID": project["ProjectID"],
                "ProjectCode": project["ProjectCode"],
                "StaffID": member["StaffID"],
                "StaffName": member["StaffName"],
                "StaffUPN": member["StaffUPN"],
                "RoleName": random.choice(ROLE_NAMES),
                "RoleType": random.choice(["Delivery", "Advisory", "Technical Assurance", "Management"]),
                "DisciplineName": member["DisciplineName"],
                "HoursBooked": round(random.uniform(120, 1800), 2),
                "ChargeabilityPct": round(random.uniform(45, 95), 2),
                "StartDate": project["StartDate"],
                "EndDate": project["EndDate"],
                "Current": project["ProjectStatusDesc"] in ("Active", "Mobilising"),
            })

    financials = []
    for project in projects:
        total_fee = project["TotalFee"]
        earned = 0.0
        billed = 0.0
        for q in range(4):
            earned += total_fee * random.uniform(0.08, 0.18)
            billed += total_fee * random.uniform(0.07, 0.16)
            forecast = max(total_fee, earned + total_fee * random.uniform(0.08, 0.35))
            cost_forecast = forecast * random.uniform(0.72, 0.93)
            margin_pct = round(((forecast - cost_forecast) / forecast) * 100, 2)
            financials.append({
                "ProjectID": project["ProjectID"],
                "ShortTitle": project["ShortTitle"],
                "AsOfDate": (TODAY - dt.timedelta(days=90 * (3 - q))).isoformat(),
                "FeeBudget": total_fee,
                "FeeForecast": round(forecast, 2),
                "FeeEarned": round(min(earned, forecast), 2),
                "FeeBilled": round(min(billed, forecast), 2),
                "CostForecast": round(cost_forecast, 2),
                "MarginForecastPct": margin_pct,
                "ChangeValue": round(total_fee * random.uniform(0.0, 0.12), 2),
                "RiskValue": round(total_fee * random.uniform(0.0, 0.08), 2),
            })

    discipline_stats = []
    for project in projects:
        used = random.sample(DISCIPLINES, k=random.randint(3, 6))
        for disc in used:
            discipline_stats.append({
                "ProjectID": project["ProjectID"],
                "ShortTitle": project["ShortTitle"],
                "DisciplineName": disc,
                "Headcount": random.randint(2, 18),
                "HoursBooked": round(random.uniform(200, 5000), 2),
                "FeeContribution": round(project["TotalFee"] * random.uniform(0.08, 0.34), 2),
                "CarbonSpecialists": random.randint(0, 4),
                "DigitalSpecialists": random.randint(0, 5),
            })

    TABLE_DATA = {
        "ENT_Staff": staff,
        "ENT_Capability": capabilities,
        "ENT_Skill": skills,
        "ENT_StaffCapability": staff_caps,
        "ENT_StaffSkill": staff_skills,
        "ENT_PracticeNetworkMembers": network_members,
        "ENT_Client": clients,
        "ENT_Programme": programmes,
        "ENT_Project": projects,
        "ENT_ProjectStaff": project_staff,
        "ENT_ProjectFinancialCumulative": financials,
        "ENT_ProjectDisciplineStatistics": discipline_stats,
    }

    return TABLE_DATA


def main() -> None:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--small", action="store_true", help="Bounded deployment sample profile")
    parser.add_argument("--out-dir", default=str(Path(__file__).parent / "_demo_data_files"))
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for table_name, rows in generate_data(small=args.small).items():
        path = out / f"{table_name}.json"
        path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        print(f"wrote {table_name}: {len(rows):,} rows -> {path}")


if __name__ == "__main__":
    main()
