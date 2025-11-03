"""
Test script for backtracking scheduler
Tests with small dataset first, then can try larger
"""

from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict
import json
import time

# Copy the scheduler code here
@dataclass
class CourseKind:
    type: str
    length: int
    lab_type: Optional[str] = None
    max_sections_together: int = 1
    ignore_capacity: bool = False

@dataclass
class Course:
    course_id: str
    name: str
    year: int
    major: Optional[str]
    kinds: List[CourseKind]
    full_year: bool = False
    is_project: bool = False

@dataclass
class Room:
    room_id: str
    type: str
    capacity: int
    building: str

@dataclass
class Instructor:
    instr_id: str
    name: str
    role: str
    qualified_courses: List[str]

@dataclass
class Group:
    group_id: str
    year: int
    specialization: Optional[str]
    sections_count: int
    students_count: int

@dataclass
class Section:
    section_id: str
    group_id: str
    students_count: int

@dataclass
class Assignment:
    course_id: str
    session_type: str
    sections: List[str]
    instructor_id: str
    room_id: str
    day: int
    period: int
    duration: int

class BacktrackingScheduler:
    def __init__(self, data: Dict):
        self.data = data
        
        # Parse data
        self.rooms = [Room(**r) for r in data['rooms']]
        self.instructors = [Instructor(**i) for i in data['instructors']]
        self.groups = [Group(**g) for g in data['groups']]
        self.sections = [Section(**s) for s in data['sections']]
        self.courses = []
        
        for c in data['courses']:
            kinds = []
            for k in c['kinds']:
                kinds.append(CourseKind(
                    type=k['type'],
                    length=k['length'],
                    lab_type=k.get('lab_type'),
                    max_sections_together=k.get('max_sections_together', 1),
                    ignore_capacity=k.get('ignore_capacity', False)
                ))
            
            self.courses.append(Course(
                course_id=c['course_id'],
                name=c['name'],
                year=c['year'],
                major=c.get('major'),
                kinds=kinds,
                full_year=c.get('full_year', False),
                is_project=c.get('is_project', False)
            ))
        
        self.DAYS = 5
        self.PERIODS_PER_DAY = 8
        
        self._build_indexes()
        
        self.timetable = {}
        self.instructor_busy = set()
        self.room_busy = set()
        self.scheduled_courses = defaultdict(set)
        
        self.attempts = 0
        self.backtracks = 0
    
    def _build_indexes(self):
        self.room_by_id = {r.room_id: r for r in self.rooms}
        self.instructor_by_id = {i.instr_id: i for i in self.instructors}
        self.group_by_id = {g.group_id: g for g in self.groups}
        self.section_by_id = {s.section_id: s for s in self.sections}
        self.course_by_id = {c.course_id: c for c in self.courses}
        
        self.sections_by_group = defaultdict(list)
        self.groups_by_year = defaultdict(list)
        self.sections_by_year = defaultdict(list)
        
        for section in self.sections:
            self.sections_by_group[section.group_id].append(section)
        
        for group in self.groups:
            self.groups_by_year[group.year].append(group)
            for section in self.sections_by_group[group.group_id]:
                self.sections_by_year[group.year].append(section)
    
    def _get_qualified_instructors(self, course_id: str, session_type: str) -> List[str]:
        qualified = []
        for instr in self.instructors:
            if course_id not in instr.qualified_courses:
                continue
            
            if session_type == "Lecture" and instr.role == "Professor":
                qualified.append(instr.instr_id)
            elif session_type in ["Tut", "Lab"] and instr.role == "TA":
                qualified.append(instr.instr_id)
        
        return qualified
    
    def _get_suitable_rooms(self, session_type: str, students_count: int, 
                           lab_type: Optional[str], ignore_capacity: bool) -> List[str]:
        suitable = []
        
        for room in self.rooms:
            if not ignore_capacity and room.capacity < students_count:
                continue
            
            if session_type == "Lab":
                if lab_type and room.type == lab_type:
                    suitable.append(room.room_id)
            elif session_type == "Lecture":
                if ignore_capacity:
                    if room.type == "theater":
                        suitable.append(room.room_id)
                else:
                    if room.type in ["classroom", "theater"]:
                        suitable.append(room.room_id)
            elif session_type == "Tut":
                if room.type == "classroom":
                    suitable.append(room.room_id)
        
        return suitable
    
    def _is_valid_assignment(self, sections: List[str], day: int, period: int, 
                            duration: int, instructor_id: str, room_id: str) -> bool:
        if duration == 2 and period % 2 != 0:
            return False
        
        if period + duration > self.PERIODS_PER_DAY:
            return False
        
        for section_id in sections:
            for p in range(period, period + duration):
                if (section_id, day, p) in self.timetable:
                    return False
        
        for p in range(period, period + duration):
            if (instructor_id, day, p) in self.instructor_busy:
                return False
        
        if room_id != "N/A":
            for p in range(period, period + duration):
                if (room_id, day, p) in self.room_busy:
                    return False
        
        return True
    
    def _place_assignment(self, assignment: Assignment):
        for section_id in assignment.sections:
            for p in range(assignment.period, assignment.period + assignment.duration):
                self.timetable[(section_id, assignment.day, p)] = assignment
            self.scheduled_courses[section_id].add(assignment.course_id)
        
        for p in range(assignment.period, assignment.period + assignment.duration):
            self.instructor_busy.add((assignment.instructor_id, assignment.day, p))
            if assignment.room_id != "N/A":
                self.room_busy.add((assignment.room_id, assignment.day, p))
    
    def _remove_assignment(self, assignment: Assignment):
        for section_id in assignment.sections:
            for p in range(assignment.period, assignment.period + assignment.duration):
                if (section_id, assignment.day, p) in self.timetable:
                    del self.timetable[(section_id, assignment.day, p)]
            self.scheduled_courses[section_id].discard(assignment.course_id)
        
        for p in range(assignment.period, assignment.period + assignment.duration):
            self.instructor_busy.discard((assignment.instructor_id, assignment.day, p))
            if assignment.room_id != "N/A":
                self.room_busy.discard((assignment.room_id, assignment.day, p))
    
    def _get_target_sections(self, course: Course, kind: CourseKind, 
                            reference_section: Section) -> List[List[str]]:
        if course.is_project:
            return [[s.section_id for s in self.sections_by_group[reference_section.group_id]]]
        
        if course.full_year:
            if kind.type == "Lecture":
                return [[s.section_id for s in self.sections_by_year[course.year]]]
            elif kind.type == "Lab":
                return [[s.section_id for s in self.sections_by_year[course.year]]]
            else:
                return [[s.section_id] for s in self.sections_by_year[course.year]]
        
        if kind.type == "Lecture":
            groups = [g for g in self.groups 
                     if g.year == course.year and 
                     (course.major is None or g.specialization == course.major)]
            return [[s.section_id for s in self.sections_by_group[g.group_id]] for g in groups]
        
        elif kind.type == "Tut":
            sections = [s for s in self.sections 
                       if self.group_by_id[s.group_id].year == course.year and
                       (course.major is None or self.group_by_id[s.group_id].specialization == course.major)]
            return [[s.section_id] for s in sections]
        
        elif kind.type == "Lab":
            sections = [s for s in self.sections 
                       if self.group_by_id[s.group_id].year == course.year and
                       (course.major is None or self.group_by_id[s.group_id].specialization == course.major)]
            
            max_per_lab = kind.max_sections_together
            groups = []
            current_group = []
            
            for section in sections:
                if len(current_group) < max_per_lab:
                    current_group.append(section.section_id)
                else:
                    groups.append(current_group)
                    current_group = [section.section_id]
            
            if current_group:
                groups.append(current_group)
            
            return groups if groups else [[s.section_id] for s in sections]
        
        return []
    
    def solve_by_course(self, session_idx: int = 0, all_sessions: List = None) -> bool:
        if all_sessions is None:
            all_sessions = self._get_all_sessions_to_schedule()
            print(f"Total sessions to schedule: {len(all_sessions)}")
        
        if session_idx >= len(all_sessions):
            return True
        
        course, kind, target_sections = all_sessions[session_idx]
        
        if all(course.course_id in self.scheduled_courses[sid] for sid in target_sections):
            return self.solve_by_course(session_idx + 1, all_sessions)
        
        students_count = sum(self.section_by_id[sid].students_count for sid in target_sections)
        
        qualified_instructors = self._get_qualified_instructors(course.course_id, kind.type)
        if not qualified_instructors:
            print(f"❌ No qualified instructor for {course.course_id} ({kind.type})")
            return False
        
        if course.is_project:
            suitable_rooms = ["N/A"]
        else:
            suitable_rooms = self._get_suitable_rooms(
                kind.type, students_count, kind.lab_type, kind.ignore_capacity
            )
            if not suitable_rooms:
                print(f"❌ No suitable room for {course.course_id} ({kind.type}, {students_count} students)")
                return False
        
        duration = kind.length // 45
        
        for day in range(self.DAYS):
            for period in range(self.PERIODS_PER_DAY):
                for instructor_id in qualified_instructors:
                    for room_id in suitable_rooms:
                        self.attempts += 1
                        
                        if self.attempts % 1000 == 0:
                            print(f"  Attempts: {self.attempts:,} | Session {session_idx+1}/{len(all_sessions)} | Backtracks: {self.backtracks:,}")
                        
                        if not self._is_valid_assignment(
                            target_sections, day, period, duration, instructor_id, room_id
                        ):
                            continue
                        
                        assignment = Assignment(
                            course_id=course.course_id,
                            session_type=kind.type,
                            sections=target_sections,
                            instructor_id=instructor_id,
                            room_id=room_id,
                            day=day,
                            period=period,
                            duration=duration
                        )
                        self._place_assignment(assignment)
                        
                        if self.solve_by_course(session_idx + 1, all_sessions):
                            return True
                        
                        self.backtracks += 1
                        self._remove_assignment(assignment)
        
        return False
    
    def _get_all_sessions_to_schedule(self) -> List[Tuple[Course, CourseKind, List[str]]]:
        sessions = []
        
        for course in self.courses:
            for kind in course.kinds:
                reference_section = self.sections[0]
                target_groups = self._get_target_sections(course, kind, reference_section)
                
                for group in target_groups:
                    sessions.append((course, kind, group))
        
        return sessions
    
    def solve(self, max_time_seconds: int = 300) -> Dict:
        print(f"\n{'='*60}")
        print(f"BACKTRACKING SCHEDULER - COURSE-BY-COURSE")
        print(f"{'='*60}\n")
        
        start_time = time.time()
        success = self.solve_by_course()
        solve_time = time.time() - start_time
        
        print(f"\n{'='*60}")
        print(f"RESULTS")
        print(f"{'='*60}")
        print(f"Success: {success}")
        print(f"Time: {solve_time:.2f}s")
        print(f"Attempts: {self.attempts:,}")
        print(f"Backtracks: {self.backtracks:,}")
        
        if success:
            return self._extract_solution(solve_time)
        else:
            return {
                'status': 'failed',
                'message': 'No solution found',
                'solve_time': solve_time,
                'attempts': self.attempts,
                'backtracks': self.backtracks
            }
    
    def _extract_solution(self, solve_time: float) -> Dict:
        schedule = []
        seen = set()
        
        for (section_id, day, period), assignment in self.timetable.items():
            key = (assignment.course_id, assignment.session_type, 
                   tuple(assignment.sections), day, assignment.period)
            if key in seen:
                continue
            seen.add(key)
            
            schedule.append({
                'course_id': assignment.course_id,
                'session_type': assignment.session_type,
                'sections': assignment.sections,
                'instructor_id': assignment.instructor_id,
                'room_id': assignment.room_id,
                'day': day,
                'start_period': assignment.period,
                'duration_periods': assignment.duration
            })
        
        return {
            'status': 'success',
            'message': 'Solution found',
            'solve_time': solve_time,
            'total_sessions': len(schedule),
            'schedule': schedule,
            'attempts': self.attempts,
            'backtracks': self.backtracks
        }

# ==================== TEST DATA ====================

# Small test dataset
SMALL_TEST_DATA = {
    "rooms": [
        {"room_id": "R1", "type": "classroom", "capacity": 100, "building": "B1"},
        {"room_id": "R2", "type": "classroom", "capacity": 50, "building": "B1"},
        {"room_id": "T1", "type": "theater", "capacity": 200, "building": "B1"},
    ],
    
    "instructors": [
        {"instr_id": "P1", "name": "Prof A", "role": "Professor", "qualified_courses": ["C1", "C2"]},
        {"instr_id": "T1", "name": "TA A", "role": "TA", "qualified_courses": ["C1", "C2"]},
    ],
    
    "groups": [
        {"group_id": "G1", "year": 1, "specialization": None, "sections_count": 2, "students_count": 40},
    ],
    
    "sections": [
        {"section_id": "G1-S1", "group_id": "G1", "students_count": 20},
        {"section_id": "G1-S2", "group_id": "G1", "students_count": 20},
    ],
    
    "courses": [
        {
            "course_id": "C1",
            "name": "Course 1",
            "year": 1,
            "major": None,
            "kinds": [
                {"type": "Lecture", "length": 90}
            ]
        },
        {
            "course_id": "C2",
            "name": "Course 2",
            "year": 1,
            "major": None,
            "kinds": [
                {"type": "Lecture", "length": 90},
                {"type": "Tut", "length": 45}
            ]
        # },
        # {
        #     "course_id": "C3",
        #     "name": "Course 3",
        #     "year": 1,
        #     "major": None,
        #     "kinds": [
        #         {"type": "Lecture", "length": 90},
        #         {"type": "Tut", "length": 45}
        #     ]
        # },
        # {
        #     "course_id": "C4",
        #     "name": "Course 4",
        #     "year": 1,
        #     "major": None,
        #     "kinds": [
        #         {"type": "Lecture", "length": 90},
        #         {"type": "Tut", "length": 45}
        #     ]
        # },
        # {
        #     "course_id": "C5",
        #     "name": "Course 5",
        #     "year": 1,
        #     "major": None,
        #     "kinds": [
        #         {"type": "Lecture", "length": 90},
        #         {"type": "Tut", "length": 45}
        #     ]
        }
    ]
}

# ==================== RUN TESTS ====================

if __name__ == "__main__":
    print("="*60)
    print("TESTING BACKTRACKING SCHEDULER")
    print("="*60)
    
    # Test 1: Small dataset
    print("\n\n🧪 TEST 1: Small Dataset (2 courses, 1 group, 2 sections)")
    print("-" * 60)
    
    scheduler = BacktrackingScheduler(SMALL_TEST_DATA)
    result = scheduler.solve(max_time_seconds=60)
    
    if result['status'] == 'success':
        print(f"\n✅ SUCCESS!")
        print(f"   Sessions scheduled: {result['total_sessions']}")
        print(f"   Time: {result['solve_time']:.2f}s")
        print(f"   Attempts: {result['attempts']:,}")
        print(f"   Backtracks: {result['backtracks']:,}")
        
        print("\n📋 Schedule Preview:")
        for i, session in enumerate(result['schedule'][:5], 1):
            print(f"   {i}. {session['course_id']} ({session['session_type']}) - "
                  f"Day {session['day']} Period {session['start_period']} - "
                  f"Sections: {session['sections']}")
    else:
        print(f"\n❌ FAILED: {result['message']}")
    
    print("\n" + "="*60)
    print("Test complete!")
