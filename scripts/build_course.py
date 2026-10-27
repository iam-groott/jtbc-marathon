"""GPX 파일로 지도용 코스 데이터(data/course-<코스>.json)를 만든다. (2026 JTBC 마라톤: 풀코스 / 10km)

- 전체 경로를 약 20m 간격으로 줄여서 저장
- 날씨 지점(출발·중간·도착) 좌표와 기상청 격자(nx, ny)를 계산
- GPS 기록 거리를 공식 거리(42.195km / 10km)에 맞춰 비례 보정
- 고저도 그래프용 거리·고도 배열, 5km(10km 코스는 2.5km)마다 구간 표지도 함께 저장

사용법: python scripts/build_course.py full   (또는 10k, all)
"""
import json
import logging
import math
import sys
import xml.etree.ElementTree as ET

from kma_grid import latlon_to_grid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_course")

GPX_NS = "{http://www.topografix.com/GPX/1/1}"
SIMPLIFY_STEP_M = 20.0

# 코스별 설정. 풀코스 날씨 지점 km는 공식 코스도의 지명 위치를 GPX 경로에서 찾은 값(±0.2km).
# 날씨 지점은 좌표(lat, lon)가 있으면 그 좌표, 없으면 km 위치를 경로에서 찾는다
# raceStart: 출발 / raceEnd: 마감(제한 시간) / serviceEnd: 이 시각 이후 날씨 수집을 멈추고 마무리 인사 표시
COURSES = {
    "full": {
        "gpx": "data/course-full.gpx",
        "out": "data/course-full.json",
        "name": "2026 JTBC 마라톤 풀코스",
        "shortName": "풀코스",
        "officialKm": 42.195,
        "raceStart": "2026-11-01T07:30:00+09:00",
        "raceEnd": "2026-11-01T14:00:00+09:00",
        "serviceEnd": "2026-11-01T14:00:00+09:00",
        "markerStepKm": 5.0,
        # 처음 지도 화면의 여백(px): 작을수록 코스가 크게 보임. 풀코스는 지점 이름표 자리만 남기고 좁게
        "mapPadding": {"topLeft": [60, 30], "bottomRight": [110, 24]},
        "checkpoints": [
            {"id": "start", "name": "광화문광장", "km": 0.0, "kmLabel": "0km 출발", "tabName": "광화문", "tabKm": "0km",
             "desc": "광화문광장(출발)", "labelDir": "top"},
            {"id": "sinseol", "name": "신설동역", "km": 20.0, "kmLabel": "약 20.0km", "tabName": "신설동", "tabKm": "20km",
             "desc": "신설동역 부근", "labelDir": "right"},
            {"id": "gunja", "name": "군자역", "km": 25.4, "kmLabel": "약 25.4km", "tabName": "군자", "tabKm": "25.4km",
             "desc": "군자역 부근", "labelDir": "right"},
            {"id": "kondae", "name": "건대입구역", "km": 27.4, "kmLabel": "약 27.4km", "tabName": "건대입구", "tabKm": "27.4km",
             "desc": "건대입구역 부근", "labelDir": "right"},
            {"id": "seokchon", "name": "석촌호수 사거리", "km": 32.5, "kmLabel": "약 32.5km", "tabName": "석촌호수", "tabKm": "32.5km",
             # labelOffset [x, y]: 이름표를 기본 위치에서 더 옮길 때(px). 석촌호수는 학여울 점과 헷갈리지 않게 위로
             "desc": "석촌호수 사거리 부근", "labelDir": "left", "labelOffset": [-10, -14]},
            {"id": "hakyeoul", "name": "학여울역", "km": 35.5, "kmLabel": "약 35.5km", "tabName": "학여울", "tabKm": "35.5km",
             # 학여울은 왼쪽으로: 아래·오른쪽 아래에 두면 바로 옆 수서IC 점에 걸려 수서IC 이름표처럼 보임
             "desc": "학여울역 부근", "labelDir": "left"},
            {"id": "suseo", "name": "수서IC", "km": 38.1, "kmLabel": "약 38.1km", "tabName": "수서IC", "tabKm": "38.1km",
             "desc": "수서IC 부근", "labelDir": "right"},
            {"id": "finish", "name": "올림픽공원", "km": 42.195, "kmLabel": "42.195km 도착", "tabName": "올림픽공원", "tabKm": "42.195km",
             "desc": "올림픽공원(도착)", "labelDir": "right"},
        ],
    },
    "10k": {
        "gpx": "data/course-10k.gpx",
        "out": "data/course-10k.json",
        "name": "2026 JTBC 마라톤 10km",
        "shortName": "10km",
        "officialKm": 10.0,
        "raceStart": "2026-11-01T07:30:00+09:00",
        "raceEnd": "2026-11-01T10:00:00+09:00",
        "serviceEnd": "2026-11-01T10:00:00+09:00",
        "markerStepKm": 2.5,
        "checkpoints": [
            {"id": "start", "name": "월드컵공원", "km": 0.0, "kmLabel": "0km 출발", "tabName": "상암", "tabKm": "0km",
             "desc": "상암 월드컵공원(출발)", "labelDir": "right"},
            {"id": "k5", "name": "5km 지점", "km": 5.0, "kmLabel": "약 5km", "tabName": "5km", "tabKm": "5km",
             "desc": "5km 지점 부근", "labelDir": "left"},
            {"id": "finish", "name": "여의도공원", "km": 10.0, "kmLabel": "10km 도착", "tabName": "여의도공원",
             "tabKm": "10km", "desc": "여의도공원(도착)", "labelDir": "right"},
        ],
    },
}


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def read_gpx(path):
    tree = ET.parse(path)
    points = []
    for trkpt in tree.getroot().iter(GPX_NS + "trkpt"):
        ele_node = trkpt.find(GPX_NS + "ele")
        ele = float(ele_node.text) if ele_node is not None else 0.0
        points.append({"lat": float(trkpt.get("lat")), "lon": float(trkpt.get("lon")), "ele": ele})
    return points


def build(course_id):
    conf = COURSES[course_id]
    gpx_path = conf["gpx"]
    out_path = conf["out"]
    official_km = conf["officialKm"]
    points = read_gpx(gpx_path)
    if len(points) < 2:
        logger.error("GPX 포인트가 부족합니다: %s", gpx_path)
        sys.exit(1)

    # 누적 거리 계산
    cum = [0.0]
    for i in range(1, len(points)):
        prev = points[i - 1]
        cur = points[i]
        cum.append(cum[-1] + haversine_m(prev["lat"], prev["lon"], cur["lat"], cur["lon"]))
    gps_total_m = cum[-1]
    scale = official_km * 1000.0 / gps_total_m
    cum = [c * scale for c in cum]
    total_m = cum[-1]
    logger.info("포인트 %d개, GPS 거리 %.2fkm → 공식 거리 %.3fkm로 보정(x%.4f)",
                len(points), gps_total_m / 1000, total_m / 1000, scale)

    # 경로 단순화 (일정 거리마다 1개)
    track = []
    profile = []
    last_m = -SIMPLIFY_STEP_M
    for i in range(len(points)):
        if cum[i] - last_m >= SIMPLIFY_STEP_M or i == len(points) - 1:
            track.append([round(points[i]["lat"], 6), round(points[i]["lon"], 6)])
            profile.append([round(cum[i] / 1000, 3), points[i]["ele"]])
            last_m = cum[i]

    # 체크포인트
    checkpoints = []
    for spec in conf["checkpoints"]:
        km = spec["km"]
        if "lat" in spec and "lon" in spec:
            # 지정 좌표 사용. 해발은 경로에서 가장 가까운 점의 고도를 씀
            idx = min(range(len(points)),
                      key=lambda j: haversine_m(spec["lat"], spec["lon"], points[j]["lat"], points[j]["lon"]))
            p = {"lat": spec["lat"], "lon": spec["lon"], "ele": points[idx]["ele"]}
            gap_m = haversine_m(spec["lat"], spec["lon"], points[idx]["lat"], points[idx]["lon"])
            logger.info("%s: 경로까지 거리 %.0fm, 경로상 %.2fkm 지점", spec["name"], gap_m, cum[idx] / 1000)
        else:
            target = km * 1000.0
            idx = len(points) - 1
            for j in range(len(cum)):
                if cum[j] >= target:
                    idx = j
                    break
            p = points[idx]
        nx, ny = latlon_to_grid(p["lat"], p["lon"])
        checkpoints.append({
            "id": spec["id"],
            "name": spec["name"],
            "km": km,
            "kmLabel": spec["kmLabel"],
            "tabName": spec["tabName"],
            "tabKm": spec["tabKm"],
            "labelDir": spec.get("labelDir", "top"),
            "labelOffset": spec.get("labelOffset"),
            "desc": spec["desc"],
            "lat": round(p["lat"], 6),
            "lon": round(p["lon"], 6),
            "ele": p["ele"],
            "nx": nx,
            "ny": ny,
        })
        logger.info("%s(%.1fkm) (%.6f, %.6f) → 격자 nx=%d, ny=%d", spec["name"], km, p["lat"], p["lon"], nx, ny)

    # 구간 표지(5km 또는 2.5km마다, 출발·도착 제외): 화면의 'CP 정보' 자리에 거리·고도·날씨로 보여 줌.
    # 대회 공식 CP(급수대 등) 정보가 없어서 items 는 비워 둠
    aid = []
    step = conf["markerStepKm"]
    marker_km = step
    while marker_km < official_km - 0.05:
        spec = {"km": round(marker_km, 1), "items": []}
        marker_km += step
        target = spec["km"] * 1000.0
        idx = len(points) - 1
        for j in range(len(cum)):
            if cum[j] >= target:
                idx = j
                break
        nx, ny = latlon_to_grid(points[idx]["lat"], points[idx]["lon"])
        aid.append({
            "km": spec["km"],
            "items": spec["items"],
            "lat": round(points[idx]["lat"], 6),
            "lon": round(points[idx]["lon"], 6),
            "ele": points[idx]["ele"],
            # 구간 날씨용 기상청 격자 (fetch_weather.py 가 지점 격자와 함께 수집)
            "nx": nx,
            "ny": ny,
        })
    logger.info("구간 표지 %d곳 위치 계산", len(aid))

    course = {
        "id": course_id,
        "name": conf["name"],
        "shortName": conf["shortName"],
        "raceStart": conf["raceStart"],
        "raceEnd": conf["raceEnd"],
        # 이 시각 이후로는 페이지가 날씨 대신 마무리 인사를 보여 준다 (수집은 두 코스 중 늦은 시각까지)
        "serviceEnd": conf["serviceEnd"],
        "totalKm": official_km,
        # 처음 지도 화면 여백 (없으면 화면 기본값)
        "mapPadding": conf.get("mapPadding"),
        "track": track,
        "profile": profile,
        "checkpoints": checkpoints,
        "aid": aid,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(course, f, ensure_ascii=False, separators=(",", ":"))
    logger.info("저장 완료: %s (경로 %d포인트)", out_path, len(track))


if __name__ == "__main__":
    if len(sys.argv) != 2 or (sys.argv[1] not in COURSES and sys.argv[1] != "all"):
        logger.error("사용법: python scripts/build_course.py full|10k|all")
        sys.exit(1)
    targets = list(COURSES.keys()) if sys.argv[1] == "all" else [sys.argv[1]]
    for cid in targets:
        build(cid)
