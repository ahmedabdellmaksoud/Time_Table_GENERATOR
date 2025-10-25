# corrected_timetable_lab_from_course.py
"""
Timetable generator using OR-Tools CP-SAT.
Lab type must be provided in course definition (kinds[].lab_type) for Lab kinds.
"""

from ortools.sat.python import cp_model
from collections import defaultdict
import time, json

# Config
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]
PERIODS_PER_DAY = 4
BASE_SLOT_MINUTES = 45
SLOTS_PER_DAY = PERIODS_PER_DAY * (90 // BASE_SLOT_MINUTES)

def subslots_needed(length_minutes, base=BASE_SLOT_MINUTES):
    if length_minutes % base != 0:
        raise ValueError("length_minutes must be multiple of base slot minutes")
    return length_minutes // base

def valid_room_types_for_session(session_type):
    mapping = {
        "Lecture": ["classroom", "theater", "hall"],
        "Tut": ["classroom", "computer lab", "hall"],
        "Lab": ["computer lab", "electronics lab", "physics lab", "chemistry lab", "bio lab"],
        "Project": ["hall", "theater", "classroom"]
    }
    return mapping.get(session_type, ["classroom"])

def build_index_maps(data):
    room_index = {r['room_id']: i for i, r in enumerate(data.get('rooms', []))}
    instr_index = {x['instr_id']: i for i, x in enumerate(data.get('instructors', []))}
    group_index = {g['group_id']: i for i, g in enumerate(data.get('groups', []))}
    section_index = {s['section_id']: i for i, s in enumerate(data.get('sections', []))}
    return room_index, instr_index, group_index, section_index

def generate_instances(courses, groups, sections):
    """
    Generate instances. For Lab kinds, lab_type must be specified inside the kind:
      e.g. "kinds": [{"type":"Lab","length":90,"lab_type":"bio lab"}]
    Graduation projects (is_project=True) will be converted to a full-day (360 min) session
    unless the course explicitly sets a larger length in the kind.
    """
    instances = []

    # Build quick mapping of groups by year & specialization
    for course in courses:
        year = course.get('year')
        major = course.get('major')
        is_project = course.get('is_project', False)

        # Find target groups for this course
        target_groups = [g for g in groups if g.get('year') == year and (major is None or g.get('specialization') == major)]
        target_group_ids = {g['group_id'] for g in target_groups}
        target_sections = [s for s in sections if s['group_id'] in target_group_ids]

        for kind in course.get('kinds', []):
            ktype = kind.get('type')
            klen = kind.get('length')

            # Project handling: force to full day (4 periods x 90 = 360) unless explicitly larger
            if is_project:
                project_len = klen if (klen and klen >= PERIODS_PER_DAY * 90) else PERIODS_PER_DAY * 90
                for g in target_groups:
                    instances.append({
                        'instance_id': f"{course['course_id']}_{g['group_id']}_PROJECT",
                        'course_id': course['course_id'],
                        'type': 'Project',
                        'group_id': g['group_id'],
                        'sessions_per_week': 1,
                        'length_minutes': project_len,
                        'expected_students': g.get('students_count', 0),
                        'has_instructor': False,
                        'is_project': True
                    })
                continue

            if ktype == "Lecture":
                for g in target_groups:
                    instances.append({
                        'instance_id': f"{course['course_id']}_{g['group_id']}_LEC",
                        'course_id': course['course_id'],
                        'type': 'Lecture',
                        'group_id': g['group_id'],
                        'sessions_per_week': kind.get('sessions_per_week', 2),
                        'length_minutes': klen,
                        'expected_students': g.get('students_count', 0)
                    })
            elif ktype == "Tut":
                for s in target_sections:
                    instances.append({
                        'instance_id': f"{course['course_id']}_{s['section_id']}_TUT",
                        'course_id': course['course_id'],
                        'type': 'Tut',
                        'section_id': s['section_id'],
                        'sessions_per_week': kind.get('sessions_per_week', 1),
                        'length_minutes': klen,
                        'expected_students': s.get('students_count', 0)
                    })
            elif ktype == "Lab":
                # IMPORTANT: lab_type must be provided in the course kind
                lab_type = kind.get('lab_type')  # NOW read from course definition
                for s in target_sections:
                    instances.append({
                        'instance_id': f"{course['course_id']}_{s['section_id']}_LAB",
                        'course_id': course['course_id'],
                        'type': 'Lab',
                        'section_id': s['section_id'],
                        'sessions_per_week': kind.get('sessions_per_week', 1),
                        'length_minutes': klen,
                        'lab_type': lab_type,   # may be None -> pre-check will flag
                        'expected_students': s.get('students_count', 0)
                    })
            else:
                # Unknown kind: skip or you can append as generic
                pass

    return instances

def quick_feasibility_checks(data, params):
    """
    Check for obvious issues before building the heavy CP model.
    - length multiple of base slot
    - lab instances must have lab_type specified and at least one matching room
    - there exists at least one qualified instructor for non-project instances
    """
    errors = []
    rooms = data.get('rooms', [])
    instructors = data.get('instructors', [])
    for inst in data.get('instances', []):
        L = inst.get('length_minutes')
        if L is None or L % params['base_slot_minutes'] != 0:
            errors.append(f"{inst.get('instance_id')}: invalid length_minutes {L}")

        # Rooms compatibility
        # For Labs: lab_type must be specified
        if inst.get('type') == 'Lab':
            if not inst.get('lab_type'):
                errors.append(f"{inst.get('instance_id')}: lab_type not specified in course definition (required for Lab)")
                continue  # can't check room candidates without lab_type

        found_room = False
        for r in rooms:
            # type must be acceptable for the session
            if r['type'] not in valid_room_types_for_session(inst['type']):
                continue
            if inst.get('type') == 'Lab':
                # lab_type must match exactly
                if r['type'] != inst.get('lab_type'):
                    continue
            # capacity check
            if inst.get('expected_students', 0) and r.get('capacity', 0) < inst.get('expected_students', 0):
                continue
            found_room = True
            break
        if not found_room:
            errors.append(f"{inst.get('instance_id')}: no compatible room (type/capacity)")

        # Instructor availability & qualification for non-projects
        if inst.get('type') != 'Project':
            found_instr = False
            for instr in instructors:
                if inst['course_id'] in instr.get('qualified_courses', []):
                    if inst['type'] == 'Lecture' and instr['role'] == 'Professor':
                        found_instr = True
                        break
                    if inst['type'] in ('Lab', 'Tut') and instr['role'] == 'TA':
                        found_instr = True
                        break
            if not found_instr:
                errors.append(f"{inst.get('instance_id')}: no qualified instructor (role/qualification) found")

    return errors

def build_and_solve(data, params, time_limit_seconds=30):
    errs = quick_feasibility_checks(data, params)
    if errs:
        return {"status": "INPUT_ERROR", "errors": errs}

    model = cp_model.CpModel()
    days = params['days']; periods = params['periods_per_day']; base = params['base_slot_minutes']
    slots_per_day = periods * (90 // base)
    total_subslots = len(days) * slots_per_day

    rooms = data.get('rooms', []); instructors = data.get('instructors', []); instances = data.get('instances', [])
    occurrences = []
    for inst in instances:
        cnt = inst.get('sessions_per_week', 1)
        for occ in range(cnt):
            occurrences.append({'instance': inst, 'occ': occ})
    n = len(occurrences)

    start = [None]*n; room_var=[None]*n; instr_var=[None]*n; day_var=[None]*n

    for idx, occ in enumerate(occurrences):
        inst = occ['instance']
        needed = subslots_needed(inst['length_minutes'], base)
        valid_starts = [s for s in range(0, total_subslots - needed + 1) if (s // slots_per_day) == ((s + needed - 1) // slots_per_day)]
        start[idx] = model.NewIntVarFromDomain(cp_model.Domain.FromValues(valid_starts), f"start_{idx}")

        # rooms domain: only rooms that match the session type and (for labs) the lab_type, and capacity
        room_candidates=[]
        for r_idx, r in enumerate(rooms):
            if r['type'] not in valid_room_types_for_session(inst['type']):
                continue
            if inst['type']=='Lab' and inst.get('lab_type') and r['type']!=inst.get('lab_type'):
                continue
            if inst.get('expected_students', 0) and r.get('capacity',0) < inst.get('expected_students',0):
                continue
            room_candidates.append(r_idx)
        # Fallback: if no candidate (should have been caught by pre-check), allow all rooms to avoid crash
        if not room_candidates:
            room_candidates = list(range(len(rooms))) if rooms else [0]
        room_var[idx] = model.NewIntVarFromDomain(cp_model.Domain.FromValues(room_candidates), f"room_{idx}")

        # instructor domain: only qualified & correct role
        if inst.get('type') == 'Project' or not inst.get('has_instructor', True):
            instr_var[idx] = None
        else:
            instr_candidates=[]
            for ii, instr in enumerate(instructors):
                if inst['course_id'] in instr.get('qualified_courses', []):
                    if inst['type']=='Lecture' and instr['role']=='Professor': instr_candidates.append(ii)
                    if inst['type'] in ('Lab','Tut') and instr['role']=='TA': instr_candidates.append(ii)
            instr_var[idx] = model.NewIntVarFromDomain(cp_model.Domain.FromValues(instr_candidates), f"instr_{idx}") if instr_candidates else None

        day_var[idx] = model.NewIntVar(0, len(days)-1, f"day_{idx}")

    # link start -> day
    for idx, occ in enumerate(occurrences):
        inst = occ['instance']; needed = subslots_needed(inst['length_minutes'], base)
        pairs = []
        for s in range(0, total_subslots - needed + 1):
            if (s // slots_per_day) == ((s + needed - 1) // slots_per_day):
                pairs.append((s, s // slots_per_day))
        model.AddAllowedAssignments([start[idx], day_var[idx]], pairs)

    # non-overlap constraints and instructor/room conflicts
    for i in range(n):
        for j in range(i+1, n):
            inst_i = occurrences[i]['instance']; inst_j = occurrences[j]['instance']
            len_i = subslots_needed(inst_i['length_minutes'], base); len_j = subslots_needed(inst_j['length_minutes'], base)
            share_students = False
            if inst_i.get('group_id') and inst_j.get('group_id') and inst_i['group_id']==inst_j['group_id']: share_students=True
            if inst_i.get('section_id') and inst_j.get('section_id') and inst_i['section_id']==inst_j['section_id']: share_students=True

            same_room = model.NewBoolVar(f"same_room_{i}_{j}")
            model.Add(room_var[i]==room_var[j]).OnlyEnforceIf(same_room)
            model.Add(room_var[i]!=room_var[j]).OnlyEnforceIf(same_room.Not())

            same_day = model.NewBoolVar(f"same_day_{i}_{j}")
            model.Add(day_var[i]==day_var[j]).OnlyEnforceIf(same_day)
            model.Add(day_var[i]!=day_var[j]).OnlyEnforceIf(same_day.Not())

            i_before_j = model.NewBoolVar(f"i_before_j_{i}_{j}")
            j_before_i = model.NewBoolVar(f"j_before_i_{i}_{j}")
            model.Add(start[i] + len_i <= start[j]).OnlyEnforceIf(i_before_j)
            model.Add(start[i] + len_i > start[j]).OnlyEnforceIf(i_before_j.Not())
            model.Add(start[j] + len_j <= start[i]).OnlyEnforceIf(j_before_i)
            model.Add(start[j] + len_j > start[i]).OnlyEnforceIf(j_before_i.Not())

            # If same room and same day => must not overlap
            model.AddBoolOr([i_before_j, j_before_i]).OnlyEnforceIf(same_room)

            # If share students => must not overlap (regardless of room)
            if share_students:
                model.AddBoolOr([i_before_j, j_before_i])

            # instructor conflicts: if same instructor and same day => must not overlap
            if instr_var[i] is not None and instr_var[j] is not None:
                same_instr = model.NewBoolVar(f"same_instr_{i}_{j}")
                model.Add(instr_var[i]==instr_var[j]).OnlyEnforceIf(same_instr)
                model.Add(instr_var[i]!=instr_var[j]).OnlyEnforceIf(same_instr.Not())
                model.AddBoolOr([i_before_j, j_before_i, same_instr.Not(), same_day.Not()])

            # general safety: if same room and same day they must not overlap (ensured above), add combined guard
            model.AddBoolOr([i_before_j, j_before_i, same_day.Not(), same_room.Not()])

    # project same-day grouping (if a project has multiple occurrences)
    proj_map = defaultdict(list)
    for idx, occ in enumerate(occurrences):
        inst = occ['instance']
        if inst.get('type') == 'Project' and inst.get('group_id'):
            proj_map[inst['group_id']].append(idx)
    for gid, idxs in proj_map.items():
        if len(idxs) <= 1:
            continue
        ref = idxs[0]
        for other in idxs[1:]:
            model.Add(day_var[ref] == day_var[other])

    # solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 8
    t0 = time.time(); status = solver.Solve(model); t1 = time.time()

    status_map = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE", cp_model.INFEASIBLE: "INFEASIBLE", cp_model.UNKNOWN:"UNKNOWN"}
    st = status_map.get(status, str(status))
    result = {"status": st, "statistics": {"solver_time_seconds": round(t1-t0,3), "num_sessions": n}, "schedule": []}
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for idx in range(n):
            inst = occurrences[idx]['instance']; s = solver.Value(start[idx])
            day = s // slots_per_day; sub_in_day = s % slots_per_day
            period = (sub_in_day // (90 // BASE_SLOT_MINUTES)) + 1; subslot = sub_in_day % (90 // BASE_SLOT_MINUTES)
            room_idx = solver.Value(room_var[idx]); instr_id = None
            if instr_var[idx] is not None:
                instr_id = instructors[solver.Value(instr_var[idx])]['instr_id']
            inst_id = inst['instance_id'] + (f"#{occurrences[idx]['occ']}" if occurrences[idx]['occ'] != 0 else "")
            result['schedule'].append({
                "instance_id": inst_id, "course_id": inst.get('course_id'), "type": inst.get('type'),
                "day": DAYS[day], "period": period, "subslot": subslot, "start_subslot_index": s,
                "length_subslots": subslots_needed(inst['length_minutes'], BASE_SLOT_MINUTES),
                "room_id": rooms[room_idx]['room_id'], "room_type": rooms[room_idx]['type'],
                "instructor_id": instr_id, "group_id": inst.get('group_id'), "section_id": inst.get('section_id')
            })
    else:
        result['unscheduled'] = [occurrences[idx]['instance']['instance_id'] for idx in range(n)]
    return result

if __name__ == "__main__":
    DATA = {
        "rooms": [
            {"room_id":"B18-G02","type":"classroom","capacity":30,"building":"B18"},
            {"room_id":"B18-G03","type":"classroom","capacity":100,"building":"B18"},
            {"room_id":"B18-G04","type":"classroom","capacity":30,"building":"B18"},
            {"room_id":"B18-G08","type":"classroom","capacity":100,"building":"B18"},
            {"room_id":"B18-G09","type":"classroom","capacity":100,"building":"B18"},
            {"room_id":"B18-G11","type":"classroom","capacity":30,"building":"B18"},
            {"room_id":"B18-G12","type":"classroom","capacity":30,"building":"B18"},
            {"room_id":"B18-G13","type":"classroom","capacity":100,"building":"B18"},
            {"room_id":"B18-G20","type":"classroom","capacity":50,"building":"B18"},
            {"room_id":"B18-F1.01","type":"classroom","capacity":100,"building":"B18"},
            {"room_id":"B18-F1.02","type":"classroom","capacity":30,"building":"B18"},
            {"room_id":"B18-F1.05","type":"classroom","capacity":100,"building":"B18"},
            {"room_id":"B18-F1.08","type":"classroom","capacity":30,"building":"B18"},
            {"room_id":"B18-F1.10","type":"classroom","capacity":100,"building":"B18"},
            {"room_id":"B18-F1.12","type":"classroom","capacity":30,"building":"B18"},
            {"room_id":"B18-F1.13","type":"classroom","capacity":30,"building":"B18"},
            {"room_id":"B18-F1.20","type":"classroom","capacity":30,"building":"B18"},
            {"room_id":"B07-G01","type":"classroom","capacity":130,"building":"B07"},
            {"room_id":"B07-F1.01","type":"classroom","capacity":30,"building":"B07"},
            {"room_id":"B07-F1.02","type":"classroom","capacity":30,"building":"B07"},
            {"room_id":"B07-F1.04","type":"classroom","capacity":30,"building":"B07"},
            {"room_id":"B07-F1.20","type":"classroom","capacity":30,"building":"B07"},
            {"room_id":"B07-F1.21","type":"classroom","capacity":30,"building":"B07"},
            {"room_id":"B07-F1.22","type":"classroom","capacity":30,"building":"B07"},
            {"room_id":"B07-F1.23","type":"classroom","capacity":30,"building":"B07"},
            {"room_id":"B07-F1.24","type":"classroom","capacity":40,"building":"B07"},

            {"room_id":"B18-G01","type":"computer lab","capacity":70,"building":"B18"},
            {"room_id":"B18-G05","type":"computer lab","capacity":70,"building":"B18"},
            {"room_id":"B18-G14","type":"computer lab","capacity":70,"building":"B18"},
            {"room_id":"B18-G17","type":"computer lab","capacity":70,"building":"B18"},
            {"room_id":"B17-G01","type":"computer lab","capacity":70,"building":"B17"},
            {"room_id":"B17-G16","type":"computer lab","capacity":70,"building":"B17"},
            {"room_id":"B17-G17","type":"computer lab","capacity":70,"building":"B17"},
            {"room_id":"B17-G22","type":"computer lab","capacity":70,"building":"B17"},
            {"room_id":"COE-PHY-LAB","type":"physics lab","capacity":30,"building":"COE"},
            {"room_id":"B7-F1.04","type":"electronics lab","capacity":30,"building":"B7"},
            {"room_id":"B7-G16","type":"computer lab","capacity":70,"building":"B07"},
            {"room_id":"B7-G12","type":"computer lab","capacity":70,"building":"B07"},

            {"room_id":"Theater-B25-F1-19","type":"theater","capacity":200,"building":"B25"},
            {"room_id":"Theater-B18-F1-19","type":"theater","capacity":200,"building":"B18"},
            {"room_id":"Theater-B10","type":"theater","capacity":200,"building":"B10"},
            {"room_id":"Theater-B09","type":"theater","capacity":200,"building":"B09"},
            {"room_id":"Theater-B08","type":"theater","capacity":200,"building":"B08"},
            {"room_id":"Theater-B07","type":"theater","capacity":200,"building":"B07"},
        ],
        "instructors": [
            {"instr_id":"PROF01","name":"Dr. Reda Elbasiony","role":"Professor","qualified_courses":["CSC111","CNC312"]},
            {"instr_id":"PROF02","name":"Dr. Ayman Arafa","role":"Professor","qualified_courses":["MTH111","MTH211","ACM215","MTH121","CNC320","CSC321"]},
            {"instr_id":"PROF03","name":"Dr. Adel Fathy","role":"Professor","qualified_courses":["PHY113","PHY123"]},
            {"instr_id":"PROF04","name":"Dr. Sherine Elmotasem","role":"Professor","qualified_courses":["LRA101"]},
            {"instr_id":"PROF05","name":"Prof. Ahmed Allam","role":"Professor","qualified_courses":["ECE111"]},
            {"instr_id":"PROF06","name":"Dr. Sameh Sherif","role":"Professor","qualified_courses":["ECE111","BIF411","BIF412","BIF312","BIF322","BIF421"]},
            {"instr_id":"PROF07","name":"Dr. Ahmed Arafa","role":"Professor","qualified_courses":["CSC211","AID413","AID427","CNC411","CSC221","CNC320","CNC321"]},
            {"instr_id":"PROF08","name":"Dr. Ahmed Anter","role":"Professor","qualified_courses":["AID311","SPS315"]},
            {"instr_id":"PROF09","name":"Prof. Mostafa Soliman","role":"Professor","qualified_courses":["CSE214","CSC115","CSC315","CNC222","CNC223","CSC429","CSC113"]},
            {"instr_id":"PROF10","name":"Dr. Ahmed Abdel-Malk","role":"Professor","qualified_courses":["ECE324","MTH211","CNC325","ECE214"]},
            {"instr_id":"PROF11","name":"Dr. Ahmed Bayumi","role":"Professor","qualified_courses":["AID428","AID312","CSC415","BIF327","AID412","AID422"]},
            {"instr_id":"PROF12","name":"Dr. Hataba","role":"Professor","qualified_courses":["CNC418","CNC419","CSC317","CSE423"]},
            {"instr_id":"PROF14","name":"Prof. Samir Ahmed","role":"Professor","qualified_courses":["CNC413","CNC111","CNC311","CNC323","CNC324","CNC327","AID413"]},
            {"instr_id":"PROF15","name":"Dr. Mohamed Issa","role":"Professor","qualified_courses":["BIF425","AID417","CNC314","CSC122","BIF321","BIF323","BIF328"]},
            {"instr_id":"PROF16","name":"Dr. Mustafa AlSayed","role":"Professor","qualified_courses":["CSC426","CSC412","CNC415","CSC314","CSC414"]},
            {"instr_id":"PROF17","name":"Dr. Mohamed Akhames","role":"Professor","qualified_courses":["CSC410","CSC411","CNC324","BIF424","AID323","CSC324","AID421","CSC425","CSC427"]},
            {"instr_id":"PROF18","name":"Prof. Marghany Hassan","role":"Professor","qualified_courses":["BIF413","AID321","AID411","CSC114","CSC322","CSE312","AID111"]},
            {"instr_id":"PROF21","name":"Dr. Moustafa Mahmoud","role":"Professor","qualified_courses":["CSC121","CSC323","CNC422"]},
            {"instr_id":"PROF22","name":"Dr. Ali Kandil","role":"Professor","qualified_courses":["MTH121","ACM323","MTH211"]},
            {"instr_id":"PROF25","name":"Dr. Mostafa Mohamed","role":"Professor","qualified_courses":["AID322","AID325"]},
            {"instr_id":"PROF26","name":"Dr. Yassen","role":"Professor","qualified_courses":["AID324","CNC421"]},
            {"instr_id":"PROF29","name":"Prof. Said Sadik","role":"Professor","qualified_courses":["LRA206","LRA306"]},
            {"instr_id":"PROF30","name":"Dr. Maali Fouad","role":"Professor","qualified_courses":["LRA405"]},
            {"instr_id":"PROF32","name":"Dr. Amal Gomaa","role":"Professor","qualified_courses":["LRA410","LRA103"]},
            {"instr_id":"PROF33","name":"Dr. Kenji Tanaka","role":"TA","qualified_courses":["LRA401","LRA402","LRA403"]},
            {"instr_id":"PROF34","name":"Dr. Yumi Yamamoto","role":"TA","qualified_courses":["LRA401","LRA402","LRA404"]},
            {"instr_id":"PROF35","name":"Dr. Haruto Ito","role":"TA","qualified_courses":["LRA401","LRA403","LRA404"]},
            {"instr_id":"PROF36","name":"Dr. Drama","role":"Professor","qualified_courses":["LRA105"]},
            {"instr_id":"PROF37","name":"Prof. Eman Allam","role":"Professor","qualified_courses":["BIF311"]},
            {"instr_id":"AP01","name":"Eng. Fatma Elsayed","role":"TA","qualified_courses":["AID417","CNC311","CSC122","BIF328","CNC321","CNC422"]},
            {"instr_id":"AP02","name":"Eng. Nada Essam","role":"TA","qualified_courses":["AID411","BIF425","CSC121","CSC122","AID325"]},
            {"instr_id":"AP03","name":"Eng. Salma Alashry","role":"TA","qualified_courses":["AID321","CNC314","CSC114","CSC122","AID322","CSC321","CSC322"]},
            {"instr_id":"AP04","name":"Eng. Mariam Ismael","role":"TA","qualified_courses":["CSC111","CNC411","CSC121","CSC221","BIF321","BIF322"]},
            {"instr_id":"AP05","name":"Eng. Nada Hamdy","role":"TA","qualified_courses":["CSC111","CNC311","CSE312","BIF421","CNC422"]},
            {"instr_id":"AP06","name":"Eng. Salma Waleed","role":"TA","qualified_courses":["AID312","AID311","CSC323","CNC421","CSC317"]},
            {"instr_id":"AP07","name":"Eng. Menna Hamdi","role":"TA","qualified_courses":["CNC312","CNC111","CSC122","CSC324","AID426"]},
            {"instr_id":"AP08","name":"Eng. Omnya Ramadan","role":"TA","qualified_courses":["CSE214","CSC315","CSC115","CNC222"]},
            {"instr_id":"AP09","name":"Eng. Heba Abdelkader","role":"TA","qualified_courses":["CSE214","BIF327","AID412","AID422"]},
            {"instr_id":"AP10","name":"Eng. Nourhan Waleed","role":"TA","qualified_courses":["AID312","CNC314","CSC121","CNC324","CNC421"]},
            {"instr_id":"AP11","name":"Eng. Menna Magdy","role":"TA","qualified_courses":["CSC211","CSC317","AID323","CSE312","CSC422"]},
            {"instr_id":"AP12","name":"Eng. Nada Ahmed","role":"TA","qualified_courses":["CSC211","CSC317","AID325","CSC422"]},
            {"instr_id":"AP13","name":"Eng. Laila Ibrahim","role":"TA","qualified_courses":["CSC121","PHY113","ECE111"]},
            {"instr_id":"AP15","name":"Eng. Rana Mohamed","role":"TA","qualified_courses":["CNC223","AID324","ECE324"]},
            {"instr_id":"AP16","name":"Eng. Saeed Mostafa","role":"TA","qualified_courses":["CSC221","CNC320","AID421","CSC427"]},
            {"instr_id":"AP19","name":"Eng. Bassant Tolba","role":"TA","qualified_courses":["CNC325","ECE111"]},
            {"instr_id":"AP20","name":"Eng. Fedda Eldin","role":"TA","qualified_courses":["MTH111","MTH211","ACM215"]},
            {"instr_id":"AP21","name":"Eng. Omar Elfaramawy","role":"TA","qualified_courses":["MTH111","MTH211","ACM215"]},
            {"instr_id":"AP22","name":"Eng. Sherien","role":"TA","qualified_courses":["MTH111","MTH211","ACM215"]},
            {"instr_id":"AP23","name":"Eng. Zeina","role":"TA","qualified_courses":["CSC314","AID427","CSC426","CSC410"]},
            {"instr_id":"AP24","name":"Eng. BAS","role":"TA","qualified_courses":["BIF311"]},
            {"instr_id":"AP25","name":"Eng. Mariem Nagy","role":"TA","qualified_courses":["AID413"]},
            {"instr_id":"AP26","name":"Eng. Omnia Shehata","role":"TA","qualified_courses":["AID428"]},
            {"instr_id":"AP27","name":"Eng. Sama Osama","role":"TA","qualified_courses":["CNC419"]},
            {"instr_id":"AP28","name":"Eng. Aya Tarek","role":"TA","qualified_courses":["CNC41","CNC418","CNC413","CSC412"]},
            {"instr_id":"AP29","name":"Eng. yyy","role":"TA","qualified_courses":["CNC415"]},
            {"instr_id":"AP30","name":"Eng. Nouran Moussa","role":"TA","qualified_courses":["BIF424","CSC414","CSC415","BIF413"]},
            {"instr_id":"AP31","name":"Eng. xxx","role":"TA","qualified_courses":["CSC411"]},
            {"instr_id":"AP32","name":"Eng. ECE","role":"TA","qualified_courses":["BIF412","BIF411"]},
        ],
        "groups": [
            {"group_id":"Y1-G1","year":1,"specialization":None,"sections_count":3,"students_count":60},
            {"group_id":"Y1-G2","year":1,"specialization":None,"sections_count":3,"students_count":60},
            {"group_id":"Y1-G3","year":1,"specialization":None,"sections_count":3,"students_count":60},
            {"group_id":"Y1-G4","year":1,"specialization":None,"sections_count":3,"students_count":60},
            {"group_id":"Y2-G1","year":2,"specialization":None,"sections_count":3,"students_count":60},
            {"group_id":"Y2-G2","year":2,"specialization":None,"sections_count":3,"students_count":60},
            {"group_id":"Y2-G3","year":2,"specialization":None,"sections_count":3,"students_count":60},
            {"group_id":"Y3-G1","year":3,"specialization":"AID","sections_count":4,"students_count":80},
            {"group_id":"Y3-G2","year":3,"specialization":"CNC","sections_count":4,"students_count":80},
            {"group_id":"Y3-G3","year":3,"specialization":"CSC","sections_count":1,"students_count":28},
            {"group_id":"Y3-G4","year":3,"specialization":"BIF","sections_count":1,"students_count":10},
            {"group_id":"Y4-G1","year":4,"specialization":"AID","sections_count":4,"students_count":80},
            {"group_id":"Y4-G2","year":4,"specialization":"CNC","sections_count":3,"students_count":60},
            {"group_id":"Y4-G3","year":4,"specialization":"CSC","sections_count":1,"students_count":28},
            {"group_id":"Y4-G4","year":4,"specialization":"BIF","sections_count":1,"students_count":10}
        ],
        "sections": [
            {"section_id":"Y1-G1-S1","group_id":"Y1-G1","students_count":20},
            {"section_id":"Y1-G1-S2","group_id":"Y1-G1","students_count":20},
            {"section_id":"Y1-G1-S3","group_id":"Y1-G1","students_count":20},
            {"section_id":"Y1-G2-S1","group_id":"Y1-G2","students_count":20},
            {"section_id":"Y1-G2-S2","group_id":"Y1-G2","students_count":20},
            {"section_id":"Y1-G2-S3","group_id":"Y1-G2","students_count":20},
            {"section_id":"Y1-G3-S1","group_id":"Y1-G3","students_count":20},
            {"section_id":"Y1-G3-S2","group_id":"Y1-G3","students_count":20},
            {"section_id":"Y1-G3-S3","group_id":"Y1-G3","students_count":20},
            {"section_id":"Y1-G4-S1","group_id":"Y1-G4","students_count":20},
            {"section_id":"Y1-G4-S2","group_id":"Y1-G4","students_count":20},
            {"section_id":"Y1-G4-S3","group_id":"Y1-G4","students_count":20},

            {"section_id":"Y2-G1-S1","group_id":"Y2-G1","students_count":20},
            {"section_id":"Y2-G1-S2","group_id":"Y2-G1","students_count":20},
            {"section_id":"Y2-G1-S3","group_id":"Y2-G1","students_count":20},
            {"section_id":"Y2-G2-S1","group_id":"Y2-G2","students_count":20},
            {"section_id":"Y2-G2-S2","group_id":"Y2-G2","students_count":20},
            {"section_id":"Y2-G2-S3","group_id":"Y2-G2","students_count":20},
            {"section_id":"Y2-G3-S1","group_id":"Y2-G3","students_count":20},
            {"section_id":"Y2-G3-S2","group_id":"Y2-G3","students_count":20},
            {"section_id":"Y2-G3-S3","group_id":"Y2-G3","students_count":20},

            {"section_id":"Y3-G1-S1","group_id":"Y3-G1","students_count":20},
            {"section_id":"Y3-G1-S2","group_id":"Y3-G1","students_count":20},
            {"section_id":"Y3-G1-S3","group_id":"Y3-G1","students_count":20},
            {"section_id":"Y3-G1-S4","group_id":"Y3-G1","students_count":20},
            {"section_id":"Y3-G2-S1","group_id":"Y3-G2","students_count":20},
            {"section_id":"Y3-G2-S2","group_id":"Y3-G2","students_count":20},
            {"section_id":"Y3-G2-S3","group_id":"Y3-G2","students_count":20},
            {"section_id":"Y3-G2-S4","group_id":"Y3-G2","students_count":20},
            {"section_id":"Y3-G3-S1","group_id":"Y3-G3","students_count":28},
            {"section_id":"Y3-G4-S1","group_id":"Y3-G4","students_count":10},

            {"section_id":"Y4-G1-S1","group_id":"Y4-G1","students_count":20},
            {"section_id":"Y4-G1-S2","group_id":"Y4-G1","students_count":20},
            {"section_id":"Y4-G1-S3","group_id":"Y4-G1","students_count":20},
            {"section_id":"Y4-G1-S4","group_id":"Y4-G1","students_count":20},
            {"section_id":"Y4-G2-S1","group_id":"Y4-G2","students_count":20},
            {"section_id":"Y4-G2-S2","group_id":"Y4-G2","students_count":20},
            {"section_id":"Y4-G2-S3","group_id":"Y4-G2","students_count":20},
            {"section_id":"Y4-G3-S1","group_id":"Y4-G3","students_count":28},
            {"section_id":"Y4-G4-S1","group_id":"Y4-G4","students_count":10},
        ],
        "courses": [
            {"course_id":"LRA401","name":"Japanese Language (1)","year":1,"major": None,"kinds":[{"type":"Tut","length":90}]},
            {"course_id":"LRA105","name":"Theater and Drama","year":1,"major": None,"kinds":[{"type":"Lecture","length":90}]},
            {"course_id":"LRA101","name":"Japanese Culture","year":1,"major": None,"kinds":[{"type":"Lecture","length":90}]},
            {"course_id":"PHY113","name":"Physics I","year":1,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Tut","length":45},{"type":"Lab","length":90, "lab_type":"physics lab"}]},
            {"course_id":"ECE111","name":"Digital Logic Design","year":1,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Tut","length":45},{"type":"Lab","length":90, "lab_type":"electronics lab"}]},
            {"course_id":"CSC111","name":"Fundamentals of Programming","year":1,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"}]},
            {"course_id":"MTH111","name":"Math I","year":1,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Tut","length":90}]},

            {"course_id":"MTH211","name":"Probability and Statistics","year":2,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Tut","length":90}]},
            {"course_id":"ACM215","name":"Ordinary Differential Equations","year":2,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Tut","length":90}]},
            {"course_id":"CSC211","name":"Software Engineering","year":2,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"}]},
            {"course_id":"CSC114","name":"Algorithms Analysis and Design","year":2,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"}]},
            {"course_id":"CNC111","name":"Networks and Web Programming","year":2,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"}]},
            {"course_id":"LRA403","name":"Japanese Language (3)","year":2,"major": None,"kinds":[{"type":"Tut","length":90}]},
            {"course_id":"LRA306","name":"Natural Resources and Sustainability","year":2,"major": None,"kinds":[{"type":"Lecture","length":90}]},
            {"course_id":"CSE214","name":"Computer Organization","year":2,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Tut","length":45},{"type":"Lab","length":90, "lab_type":"computer lab"}]},

            {"course_id":"ECE324","name":"Digital Signal Processing","year":3,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"electronics lab"}]},
            {"course_id":"AID312","name":"Intelligent Systems","year":3,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"}]},
            {"course_id":"CSC317","name":"Computer Graphics and Visualization","year":3,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"}]},
            {"course_id":"CNC311","name":"Computer Networks","year":3,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"}]},
            {"course_id":"CNC314","name":"Database Systems","year":3,"major": None,"kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"}]},
            {"course_id":"CNC312","name":"Foundations of Information Systems","year":3,"major": "CNC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"AID311","name":"Mathematics of Data Science","year":3,"major": "AID","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CSC314","name":"Software Modeling and Analysis","year":3,"major": "CSC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"BIF311","name":"Human Biology","year":3,"major": "BIF","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CSC315","name":"Seminar and Project-Based Learning on CSIT","year":3,"major": "BIF","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"}]},

            # Year 4 Major AID AI shit
            {"course_id":"AID413","name":"Data Security","year":4,"major": "AID","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"AID427","name":"New Trends in Data Science","year":4,"major": "AID","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"AID428","name":"New Trends in AI","year":4,"major": "AID","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"AID321","name":"Machine Learning","year":4,"major": "AID","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"AID411","name":"BIG Data Analytics & Visualization","year":4,"major": "AID","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"AID417","name":"Advanced Data Mining","year":4,"major": "AID","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},

            # Year 4 Major CNC cyber shit
            {"course_id":"CNC419","name":"IT Security and Risk Management","year":4,"major": "CNC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CNC413","name":"Digital Forensics","year":4,"major": "CNC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CNC418","name":"Software Security","year":4,"major": "CNC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CNC411","name":"Fundamentals of Cybersecurity","year":4,"major": "CNC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CNC415","name":"Network Design and Management","year":4,"major": "CNC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CNC324","name":"IT Infrastructure","year":4,"major": "CNC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},

            # Year 4 Major CSC My Beloved Major
            {"course_id":"CSC414","name":"Game Design & Development","year":4,"major": "CSC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CSC415","name":"New Trends in Computer Science","year":4,"major": "CSC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CSC426","name":"Distributed Systems","year":4,"major": "CSC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CSC410","name":"Software Quality","year":4,"major": "CSC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CSC411","name":"Software Verification and Validation (V&V)","year":4,"major": "CSC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"CSC412","name":"Software Security","year":4,"major": "CSC","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},

            # Year 4 Major BIF THE PEOPLE WHO DON'T KNOW WHAT THEY ARE DOING BUT THEY DO IT ANY WAY
            {"course_id":"BIF412","name":"Management and Design of Health","year":4,"major": "BIF","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"BIF411","name":"Structural Bioinformatics","year":4,"major": "BIF","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"BIF413","name":"Algorithms in Bioinformatics","year":4,"major": "BIF","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"BIF425","name":"New Trends in Bioinformatics","year":4,"major": "BIF","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},
            {"course_id":"BIF424","name":" IT Infrastructure","year":4,"major": "BIF","kinds":[{"type":"Lecture","length":90},{"type":"Lab","length":90, "lab_type":"computer lab"},{"type":"Tut","length":45}]},

            # GRADUATION PROJECTS
            {"course_id":"CNC414","name":"Graduation Project","year":4,"major": "CNC","kinds":[{"type":"Lecture","length":90}],"is_project": True},
            {"course_id":"AID414","name":"Graduation Project","year":4,"major": "AID","kinds":[{"type":"Lecture","length":90}],"is_project": True},
            {"course_id":"CSC413","name":"Graduation Project","year":4,"major": "CSC","kinds":[{"type":"Lecture","length":90}],"is_project": True},
            {"course_id":"BIF410","name":"Graduation Project","year":4,"major": "BIF","kinds":[{"type":"Lecture","length":90}],"is_project": True},
        ],
    }


    # auto-generate instances (lab_type must be specified in kinds for Lab)
    DATA['instances'] = generate_instances(DATA['courses'], DATA['groups'], DATA['sections'])

    # debug print of last instances (optional)
    # print(DATA['instances'][-20:])

    PARAMS = {"days": DAYS, "periods_per_day": PERIODS_PER_DAY, "base_slot_minutes": BASE_SLOT_MINUTES}
    out = build_and_solve(DATA, PARAMS, time_limit_seconds=300)
    print(json.dumps(out, indent=2))
