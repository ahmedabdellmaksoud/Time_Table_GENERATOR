"""
University Timetable Scheduler - Brute Force Backtracking
Two strategies: Section-by-section and Course-by-course

Installation:
pip install fastapi uvicorn pandas openpyxl

Usage:
python backtracking_scheduler.py
"""

from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict
import json
import time
import copy

# ==================== DATA MODELS ====================

@dataclass
class CourseKind:
    type: str  # "Lecture", "Tut", "Lab"
    length: int  # in minutes
    lab_type: Optional[str] = None
    max_sections_together: int = 1  # For labs
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
    """Represents a scheduled session"""
    course_id: str
    session_type: str
    day: int
    period: int
    sections: List[str]
    duration: int
    instructor_id: str
    room_id: str

# ==================== BACKTRACKING SCHEDULER ====================

class BacktrackingScheduler:
    def __init__(self, data: Dict):
        self.data = data

        # Parse data
        self.rooms = [Room(**r) for r in data['rooms']]
        self.instructors = [Instructor(**i) for i in data['instructors']]
        self.groups = [Group(**g) for g in data['groups']]
        self.sections = [Section(**s) for s in data['sections']]
        self.courses = []

        # Parse courses with new fields
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

        # Constants
        self.DAYS = 5
        self.PERIODS_PER_DAY = 8
        self.TOTAL_SLOTS = self.DAYS * self.PERIODS_PER_DAY

        # Build indexes
        self._build_indexes()

        # State
        self.timetable = {}  # (section_id, day, period) -> Assignment
        self.instructor_busy = set()  # (instructor_id, day, period)
        self.room_busy = set()  # (room_id, day, period)
        self.scheduled_sessions = defaultdict(set)  # section_id -> set of (course_id, session_type)

        # Statistics
        self.attempts = 0
        self.backtracks = 0

    def _build_indexes(self):
        """Build lookup indexes"""
        self.room_by_id = {r.room_id: r for r in self.rooms}
        self.instructor_by_id = {i.instr_id: i for i in self.instructors}
        self.group_by_id = {g.group_id: g for g in self.groups}
        self.section_by_id = {s.section_id: s for s in self.sections}
        self.course_by_id = {c.course_id: c for c in self.courses}

        # Group/year mappings
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
        """Get instructors qualified for this course"""
        qualified = []
        for instr in self.instructors:
            if course_id not in instr.qualified_courses:
                continue

            # Professors for Lectures, TAs for Labs/Tuts
            if session_type == "Lecture" and instr.role == "Professor":
                qualified.append(instr.instr_id)
            elif session_type in ["Tut", "Lab"] and instr.role == "TA":
                qualified.append(instr.instr_id)

        return qualified

    def _get_suitable_rooms(self, session_type: str, students_count: int,
                            lab_type: Optional[str], ignore_capacity: bool) -> List[str]:
        """Get rooms suitable for this session"""
        suitable = []

        for room in self.rooms:
            # Check capacity (unless ignored)
            if not ignore_capacity and room.capacity < students_count:
                continue

            # Check room type
            if session_type == "Lab":
                if lab_type and room.type == lab_type:
                    suitable.append(room.room_id)
            elif session_type == "Lecture":
                if ignore_capacity:
                    # For full-year lectures, prefer theaters
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
        """Check if assignment is valid"""
        # Check period alignment (90-min must start at even period)
        if duration == 2 and period % 2 != 0:
            return False

        # Check period bounds
        if period + duration > self.PERIODS_PER_DAY:
            return False

        # Check section conflicts
        for section_id in sections:
            for p in range(period, period + duration):
                if (section_id, day, p) in self.timetable:
                    return False

        # Check instructor conflicts
        for p in range(period, period + duration):
            if (instructor_id, day, p) in self.instructor_busy:
                return False

        # Check room conflicts (skip for graduation projects)
        if room_id != "N/A":
            for p in range(period, period + duration):
                if (room_id, day, p) in self.room_busy:
                    return False

        return True

    def _place_assignment(self, assignment: Assignment):
        """Place an assignment in the timetable"""
        for section_id in assignment.sections:
            for p in range(assignment.period, assignment.period + assignment.duration):
                self.timetable[(section_id, assignment.day, p)] = assignment
            # Track specific session type for each section
            for section_id in assignment.sections:
                self.scheduled_sessions[section_id].add((assignment.course_id, assignment.session_type))

        for p in range(assignment.period, assignment.period + assignment.duration):
            self.instructor_busy.add((assignment.instructor_id, assignment.day, p))
            if assignment.room_id != "N/A":
                self.room_busy.add((assignment.room_id, assignment.day, p))

    def _remove_assignment(self, assignment: Assignment):
        """Remove an assignment from the timetable"""
        for section_id in assignment.sections:
            for p in range(assignment.period, assignment.period + assignment.duration):
                if (section_id, assignment.day, p) in self.timetable:
                    del self.timetable[(section_id, assignment.day, p)]
            # Remove specific session type tracking
            for section_id in assignment.sections:
                self.scheduled_sessions[section_id].discard((assignment.course_id, assignment.session_type))

        for p in range(assignment.period, assignment.period + assignment.duration):
            self.instructor_busy.discard((assignment.instructor_id, assignment.day, p))
            if assignment.room_id != "N/A":
                self.room_busy.discard((assignment.room_id, assignment.day, p))

    def _get_target_sections(self, course: Course, kind: CourseKind,
                             reference_section: Section) -> List[List[str]]:
        """
        Determine which sections attend this session type together.
        Returns list of section groups (each group attends together).
        """
        if course.is_project:
            # Graduation project: one session per group
            return [[s.section_id for s in self.sections_by_group[reference_section.group_id]]]

        if course.full_year:
            # Full-year course: ALL sections of this year together
            if kind.type == "Lecture":
                # One lecture for entire year
                return [[s.section_id for s in self.sections_by_year[course.year]]]
            elif kind.type == "Lab":
                # One lab for entire year (all sections together)
                return [[s.section_id for s in self.sections_by_year[course.year]]]
            else:
                # Tutorials still per section
                return [[s.section_id] for s in self.sections_by_year[course.year]]

        # Normal courses
        if kind.type == "Lecture":
            # One lecture per group
            groups = [g for g in self.groups
                      if g.year == course.year and
                      (course.major is None or g.specialization == course.major)]
            return [[s.section_id for s in self.sections_by_group[g.group_id]] for g in groups]

        elif kind.type == "Tut":
            # One tutorial per section
            sections = [s for s in self.sections
                        if self.group_by_id[s.group_id].year == course.year and
                        (course.major is None or self.group_by_id[s.group_id].specialization == course.major)]
            return [[s.section_id] for s in sections]

        elif kind.type == "Lab":
            # Group sections based on max_sections_together
            sections = [s for s in self.sections
                        if self.group_by_id[s.group_id].year == course.year and
                        (course.major is None or self.group_by_id[s.group_id].specialization == course.major)]

            # Smart grouping
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

    def _is_course_complete_for_section(self, course: Course, section_id: str) -> bool:
        """Check if all session types for a course are scheduled for a section"""
        required_sessions = {(course.course_id, kind.type) for kind in course.kinds}
        scheduled_for_section = self.scheduled_sessions[section_id]
        return required_sessions.issubset(scheduled_for_section)

    # ==================== STRATEGY 1: SECTION-BY-SECTION ====================

    def solve_by_section(self, section_idx: int = 0) -> bool:
        """
        Backtracking by section (like C++ implementation).
        For each section, schedule all its courses before moving to next section.
        """
        if section_idx >= len(self.sections):
            return True  # All sections scheduled

        section = self.sections[section_idx]

        # Get courses this section needs
        eligible_courses = [
            c for c in self.courses
            if c.year == self.group_by_id[section.group_id].year and
               (c.major is None or c.major == self.group_by_id[section.group_id].specialization)
        ]

        # Check if all courses for this section are completely scheduled
        all_scheduled = all(
            self._is_course_complete_for_section(course, section.section_id)
            for course in eligible_courses
        )

        if all_scheduled:
            return self.solve_by_section(section_idx + 1)

        # Find an unscheduled course session
        for course in eligible_courses:
            # Skip if course is already complete for this section
            if self._is_course_complete_for_section(course, section.section_id):
                continue

            if course.is_project:
                continue

            # Try to schedule each session type that's not yet scheduled
            for kind in course.kinds:
                # Check if this session type is already scheduled for this section
                if (course.course_id, kind.type) in self.scheduled_sessions[section.section_id]:
                    continue

                target_groups = self._get_target_sections(course, kind, section)

                # Check if this session type is relevant to current section
                section_involved = any(section.section_id in group for group in target_groups)
                if not section_involved:
                    continue

                # Find the group containing current section
                target_sections = None
                for group in target_groups:
                    if section.section_id in group:
                        target_sections = group
                        break

                if not target_sections:
                    continue

                # Check if already scheduled by another section in the group
                already_scheduled = all(
                    (course.course_id, kind.type) in self.scheduled_sessions[sid]
                    for sid in target_sections
                )
                if already_scheduled:
                    # Mark as scheduled for current section and continue
                    self.scheduled_sessions[section.section_id].add((course.course_id, kind.type))
                    return self.solve_by_section(section_idx)

                # Try to schedule this session
                students_count = sum(self.section_by_id[sid].students_count for sid in target_sections)

                qualified_instructors = self._get_qualified_instructors(course.course_id, kind.type)
                if not qualified_instructors:
                    print(f"No qualified instructor for {course.course_id} ({kind.type})")
                    return False

                if course.is_project:
                    suitable_rooms = ["N/A"]
                else:
                    suitable_rooms = self._get_suitable_rooms(
                        kind.type, students_count, kind.lab_type, kind.ignore_capacity
                    )
                    if not suitable_rooms:
                        print(f"No suitable room for {course.course_id} ({kind.type}, {students_count} students)")
                        return False

                duration = kind.length // 45

                # Try all combinations
                for day in range(self.DAYS):
                    for period in range(self.PERIODS_PER_DAY):
                        for instructor_id in qualified_instructors:
                            for room_id in suitable_rooms:
                                self.attempts += 1

                                if not self._is_valid_assignment(
                                        target_sections, day, period, duration, instructor_id, room_id
                                ):
                                    continue

                                # Place assignment
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

                                # Recurse
                                if self.solve_by_section(section_idx):
                                    return True

                                # Backtrack
                                self.backtracks += 1
                                self._remove_assignment(assignment)

                # Failed to schedule this session type
                return False

        # All course sessions scheduled, move to next section
        return self.solve_by_section(section_idx + 1)

    # ==================== STRATEGY 2: COURSE-BY-COURSE ====================

    def _get_all_sessions_to_schedule(self) -> List[Tuple[Course, CourseKind, List[str]]]:
        """Generate all sessions that need to be scheduled"""
        sessions = []

        for course in self.courses:
            for kind in course.kinds:
                # Get all section groups for this course/kind
                # Use first section as reference
                reference_section = self.sections[0]
                target_groups = self._get_target_sections(course, kind, reference_section)

                for group in target_groups:
                    sessions.append((course, kind, group))

        return sessions

    def solve_by_course(self, session_idx: int = 0, all_sessions: List = None) -> bool:
        """
        Backtracking by course.
        Schedule all sessions for all courses systematically.
        """
        if all_sessions is None:
            all_sessions = self._get_all_sessions_to_schedule()

        if session_idx >= len(all_sessions):
            return True  # All sessions scheduled

        course, kind, target_sections = all_sessions[session_idx]

        # Check if already scheduled
        already_scheduled = all(
            (course.course_id, kind.type) in self.scheduled_sessions[sid]
            for sid in target_sections
        )
        if already_scheduled:
            return self.solve_by_course(session_idx + 1, all_sessions)

        students_count = sum(self.section_by_id[sid].students_count for sid in target_sections)

        qualified_instructors = self._get_qualified_instructors(course.course_id, kind.type)
        if not qualified_instructors:
            print(f"No qualified instructor for {course.course_id} ({kind.type})")
            return False

        if course.is_project:
            suitable_rooms = ["N/A"]
        else:
            suitable_rooms = self._get_suitable_rooms(
                kind.type, students_count, kind.lab_type, kind.ignore_capacity
            )
            if not suitable_rooms:
                print(f"No suitable room for {course.course_id} ({kind.type}, {students_count} students)")
                return False

        duration = kind.length // 45

        # Try all combinations
        for day in range(self.DAYS):
            for period in range(self.PERIODS_PER_DAY):
                for instructor_id in qualified_instructors:
                    for room_id in suitable_rooms:
                        self.attempts += 1

                        if not self._is_valid_assignment(
                                target_sections, day, period, duration, instructor_id, room_id
                        ):
                            continue

                        # Place assignment
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

                        # Recurse
                        if self.solve_by_course(session_idx + 1, all_sessions):
                            return True

                        # Backtrack
                        self.backtracks += 1
                        self._remove_assignment(assignment)

        return False

    # ==================== SOLVE ENTRY POINTS ====================

    def solve(self, strategy: str = "section", max_time_seconds: int = 300) -> Dict:
        """
        Main solve entry point

        Args:
            strategy: "section" or "course"
            max_time_seconds: Timeout (not enforced, just for reference)
        """
        print(f"\n{'='*60}")
        print(f"STARTING BACKTRACKING SCHEDULER")
        print(f"Strategy: {strategy.upper()}")
        print(f"{'='*60}\n")

        start_time = time.time()

        if strategy == "section":
            success = self.solve_by_section()
        else:
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
        """Extract solution from timetable with new format"""
        schedule = []
        seen = set()

        DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]

        for (section_id, day, period), assignment in self.timetable.items():
            key = (assignment.course_id, assignment.session_type,
                   tuple(assignment.sections), day, assignment.period)
            if key in seen:
                continue
            seen.add(key)

            # Create one entry per section in the assignment
            for section_id in assignment.sections:
                section = self.section_by_id[section_id]
                group = self.group_by_id[section.group_id]
                course = self.course_by_id[assignment.course_id]
                room = self.room_by_id.get(assignment.room_id, None)
                instructor = self.instructor_by_id[assignment.instructor_id]

                # Calculate time slot
                start_time_minutes = assignment.period * 45
                end_time_minutes = start_time_minutes + (assignment.duration * 45)

                start_hour = start_time_minutes // 60
                start_minute = start_time_minutes % 60
                end_hour = end_time_minutes // 60
                end_minute = end_time_minutes % 60

                time_slot = f"{start_hour:02d}:{start_minute:02d} - {end_hour:02d}:{end_minute:02d}"

                # Determine period alignment
                period_alignment = "Any"
                if assignment.duration == 2:
                    period_alignment = "Even" if assignment.period % 2 == 0 else "Odd"

                # Determine section display
                section_display = section_id.split('-')[-1]

                # Determine lab type and physics lab status
                lab_type = None
                is_physics_lab = False
                if assignment.session_type == "Lab":
                    # Find the course kind to get lab_type
                    for kind in course.kinds:
                        if kind.type == "Lab":
                            lab_type = kind.lab_type
                            is_physics_lab = (lab_type == "physics lab")
                            break

                schedule.append({
                    'instance_id': f"{assignment.course_id}_{section_id}_{assignment.session_type.upper()}",
                    'course_id': assignment.course_id,
                    'course_name': course.name,
                    'type': assignment.session_type,
                    'meeting_type': assignment.session_type,
                    'day': DAY_NAMES[day],
                    'period': period + 1,  # Convert to 1-based
                    'start_period': assignment.period + 1,  # Convert to 1-based
                    'end_period': assignment.period + assignment.duration,  # Already 1-based
                    'time_slot': time_slot,
                    'duration_periods': assignment.duration,
                    'duration_minutes': assignment.duration * 45,
                    'room_id': assignment.room_id,
                    'room_type': room.type if room else "N/A",
                    'building': room.building if room else "N/A",
                    'instructor_id': assignment.instructor_id,
                    'instructor_name': instructor.name,
                    'group_id': section.group_id,
                    'section_id': section_id,
                    'section_display': section_display,
                    'year': group.year,
                    'lab_type': lab_type,
                    'is_physics_lab': is_physics_lab,
                    'period_alignment': period_alignment
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

# ==================== API ====================

def schedule_timetable(data: Dict, strategy: str = "section",
                       max_time_seconds: int = 300) -> Dict:
    """
    Entry point for scheduling

    Args:
        data: Input data dictionary
        strategy: "section" or "course"
        max_time_seconds: Maximum solving time
    """
    try:
        scheduler = BacktrackingScheduler(data)
        result = scheduler.solve(strategy, max_time_seconds)
        return result
    except Exception as e:
        import traceback
        return {
            'status': 'error',
            'message': str(e),
            'traceback': traceback.format_exc()
        }

# ==================== TESTING ====================

if __name__ == "__main__":
    from input import DATA

    print("Choose strategy:")
    print("1. Section-by-section (like C++ code)")
    print("2. Course-by-course")

    choice = input("Enter choice (1 or 2): ").strip()
    strategy = "section" if choice == "1" else "course"

    result = schedule_timetable(DATA, strategy=strategy, max_time_seconds=600)

    print(f"\nFinal Status: {result['status']}")
    if result['status'] == 'success':
        print(f"Total Sessions: {result['total_sessions']}")

        # Export to JSON
        with open('schedule_backtracking.json', 'w') as f:
            json.dump(result, f, indent=2)
        print("Schedule exported to schedule_backtracking.json")

        # Print first few entries as example
        print("\nFirst 10 schedule entries:")
        for i, entry in enumerate(result['schedule'][:10]):
            print(f"{i+1}. {entry['instance_id']} - {entry['day']} {entry['time_slot']} - {entry['room_id']}")

        # Count sessions by type for verification
        session_counts = {}
        for entry in result['schedule']:
            course_session = f"{entry['course_id']}_{entry['type']}"
            session_counts[course_session] = session_counts.get(course_session, 0) + 1

        print("\nSession counts by course and type:")
        for session_type, count in sorted(session_counts.items()):
            print(f"  {session_type}: {count} sessions")
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")