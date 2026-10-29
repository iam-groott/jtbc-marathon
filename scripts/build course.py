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
        # 2026-10-29 공식 홈페이지 GPX (둔촌 사거리 반환 구간이 들어간 변경 코스). 고도도 공식 GPX 값 그대로 씀
        "gpx": "data/course-full.gpx",
        "out": "data/course-full.json",
        "name": "2026 JTBC 마라톤 풀코스",
        "shortName": "풀코스",
        "officialKm": 42.195,
        "raceStart": "2026-11-01T07:30:00+09:00",
        # 공식 책자 경기일정: 07:30 휠체어 출발(07:33 엘리트·마스터즈 순차 출발), 13:00 풀코스 경기 종료
        "raceEnd": "2026-11-01T13:00:00+09:00",
        "serviceEnd": "2026-11-01T13:00:00+09:00",
        # 공식 코스 지도의 급수·스펀지 위치가 2.5km 간격이라 구간 표지도 2.5km마다
        "markerStepKm": 2.5,
        # 구간 표지별 제공 품목 (2026 공식 책자 풀코스 지도 아이콘 기준, 현장 운영에 따라 바뀔 수 있음)
        "aidItems": {
            5.0: ["급수대"], 7.5: ["스펀지"], 10.0: ["급수대"], 12.5: ["스펀지"],
            15.0: ["급수대"], 17.5: ["스펀지"], 20.0: ["급수대"], 22.5: ["스펀지"],
            25.0: ["급수대", "간식"], 27.5: ["스펀지"], 30.0: ["급수대", "간식"], 32.5: ["스펀지"],
            35.0: ["급수대", "간식"], 37.5: ["스펀지"], 40.0: ["급수대"],
        },
        # 처음 지도 화면의 여백(px): 작을수록 코스가 크게 보임. 풀코스는 지점 이름표 자리만 남기고 좁게
        "mapPadding": {"topLeft": [60, 30], "bottomRight": [110, 24]},
        # 여백을 맞춰 코스 전체를 보여 준 뒤 더 확대할 줌 (0.25 = 한 단계, 확대 버튼 한 번은 0.5)
        "mapZoomIn": 0.25,
        # labelDir: 이름표 방향(top/bottom/left/right), labelOffset [x, y]: 그 방향에서 더 옮길 거리(px, 오른쪽·아래가 +)
        "checkpoints": [
            {"id": "start", "name": "광화문광장", "km": 0.0, "kmLabel": "0km", "tabName": "광화문광장", "tabKm": "0km",
             "desc": "광화문광장", "labelDir": "top", "labelOffset": [16, -10]},
            {"id": "sinseol", "name": "신설동역", "km": 17.7, "kmLabel": "약 17.7km", "tabName": "신설동역", "tabKm": "약 17.7km",
             "desc": "신설동역 부근", "labelDir": "top", "labelOffset": [40, -8]},
            {"id": "gunja", "name": "군자역", "km": 23.0, "kmLabel": "약 23.0km", "tabName": "군자역", "tabKm": "약 23km",
             "desc": "군자역 부근", "labelDir": "right"},
            {"id": "kondae", "name": "건대입구역", "km": 25.0, "kmLabel": "약 25.0km", "tabName": "건대입구역", "tabKm": "약 25km",
             "desc": "건대입구역 부근", "labelDir": "left"},
            {"id": "seokchon", "name": "석촌호수", "km": 29.8, "kmLabel": "약 29.8km", "tabName": "석촌호수", "tabKm": "약 29.8km",
             "desc": "석촌호수 사거리 부근", "labelDir": "left", "labelOffset": [-6, -13]},
            {"id": "hakyeoul", "name": "학여울역", "km": 33.3, "kmLabel": "약 33.3km", "tabName": "학여울역", "tabKm": "약 33.3km",
             "desc": "학여울역 부근", "labelDir": "bottom", "labelOffset": [-33, 6]},
            {"id": "suseo", "name": "수서IC", "km": 35.9, "kmLabel": "약 35.9km", "tabName": "수서IC", "tabKm": "약 35.9km",
             "desc": "수서IC 부근", "labelDir": "bottom", "labelOffset": [33, 10]},
            {"id": "finish", "name": "올림픽공원", "km": 42.195, "kmLabel": "42.195km", "tabName": "올림픽공원", "tabKm": "42.195km",
             "desc": "올림픽공원", "labelDir": "right", "labelOffset": [12, 14]},
        ],
    },
    "10k": {
        "gpx": "data/course-10k.gpx",
        "out": "data/course-10k.json",
        "name": "2026 JTBC 마라톤 10km",
        "shortName": "10km",
        "officialKm": 10.0,
        "raceStart": "2026-11-01T07:30:00+09:00",
        # 공식 책자 경기일정: 10:30 10K 경기 종료
        "raceEnd": "2026-11-01T10:30:00+09:00",
        "serviceEnd": "2026-11-01T10:30:00+09:00",
        # 10K 구간 정보는 출발·급수대·도착만 (0, 3.8, 7.3, 10km): 일정 간격 거리 표지 없음
        "markerStepKm": 0,
        # 거리 표지 외에 따로 넣는 표지 (2026 공식 책자 10K 코스 지도의 급수대 위치)
        "aidExtra": [
            {"km": 3.8, "items": ["급수대"]},
            {"km": 7.3, "items": ["급수대"]},
        ],
        # 처음 지도 여백: 좌우를 같게 해서 코스가 가운데 오게
        "mapPadding": {"topLeft": [105, 20], "bottomRight": [105, 14]},
        "checkpoints": [
            {"id": "start", "name": "상암월드컵공원", "km": 0.0, "kmLabel": "0km 출발", "tabName": "상암월드컵공원", "tabKm": "0km",
             "desc": "상암월드컵공원(출발)", "labelDir": "right"},
            {"id": "yanghwa", "name": "양화대교 남단", "km": 4.2, "kmLabel": "약 4.2km", "tabName": "양화대교 남단", "tabKm": "약 4.2km",
             "desc": "양화대교 남단 부근", "labelDir": "left", "labelOffset": [-4, -20]},
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

    # 구간 표지(출발 0km, 5km 또는 2.5km마다, 도착): 화면의 '구간 정보'에 거리·고도·날씨로 보여 줌.
    # 대회 공식 CP(급수대 등) 정보가 없어서 items 는 비워 둠. 출발·도착은 edge 로 구분
    specs = [{"km": 0.0, "items": [], "edge": "start"}]
    step = conf["markerStepKm"]
    marker_km = step
    # markerStepKm 이 0 이면 일정 간격 거리 표지를 만들지 않음 (aidExtra 표지만)
    while step > 0 and marker_km < official_km - 0.05:
        km_key = round(marker_km, 1)
        specs.append({"km": km_key, "items": list(conf.get("aidItems", {}).get(km_key, []))})
        marker_km += step
    # 2.5km 간격이 아닌 곳의 표지(예: 10K 급수대 3.8km·7.3km)를 더해서 거리순으로 정렬.
    # 더한 표지와 0.3km 안에 있는 빈 거리 표지는 지도에서 겹치므로 뺌 (예: 7.3km 급수대 옆 7.5km)
    extras = conf.get("aidExtra", [])
    specs = [sp for sp in specs
             if sp["items"] or sp.get("edge") or all(abs(sp["km"] - ex["km"]) > 0.3 for ex in extras)]
    for extra in extras:
        specs.append({"km": extra["km"], "items": list(extra["items"])})
    specs.sort(key=lambda x: x["km"])
    specs.append({"km": official_km, "items": [], "edge": "finish"})
    aid = []
    for spec in specs:
        target = spec["km"] * 1000.0
        idx = len(points) - 1
        for j in range(len(cum)):
            if cum[j] >= target:
                idx = j
                break
        nx, ny = latlon_to_grid(points[idx]["lat"], points[idx]["lon"])
        item = {
            "km": spec["km"],
            "items": spec["items"],
            "lat": round(points[idx]["lat"], 6),
            "lon": round(points[idx]["lon"], 6),
            "ele": points[idx]["ele"],
            # 구간 날씨용 기상청 격자 (fetch_weather.py 가 지점 격자와 함께 수집)
            "nx": nx,
            "ny": ny,
        }
        if spec.get("edge"):
            item["edge"] = spec["edge"]
        aid.append(item)
    logger.info("구간 표지 %d곳 위치 계산 (출발·도착 포함)", len(aid))

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
        "mapZoomIn": conf.get("mapZoomIn", 0),
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
