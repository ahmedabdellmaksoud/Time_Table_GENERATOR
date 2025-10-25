import json
import time
import random
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
import heapq
from flask import Flask, request, jsonify
from flask_cors import CORS

# ==================== DATA MODELS ====================
class ComponentType(Enum):
    LECTURE = "lecture"
    LAB = "lab"
    TUTORIAL = "tutorial"

class InstructorType(Enum):
    PROFESSOR = "professor"
    TA = "ta"
    PART_TIME = "part_time"

class RoomType(Enum):
    LECTURE = "lecture"
    LAB = "lab"
    CLASSROOM = "classroom"

@dataclass
class CourseComponent:
    component_id: str
    type: ComponentType
    lab_type: str = ""
    duration_slots: int = 1
    min_capacity: int = 0
    instructor_qualification: str = ""
    requires_lecture_first: bool = False
    concurrent_sections: bool = False
    student_groups: List[str] = field(default_factory=list)
    student_sections: List[str] = field(default_factory=list)
    is_scheduled: bool = False

@dataclass
class Course:
    course_id: str
    course_name: str
    course_type: str = "core"
    components: List[CourseComponent] = field(default_factory=list)
    all_year: bool = False

@dataclass
class Instructor:
    instructor_id: str
    name: str
    type: InstructorType
    qualifications: Set[str] = field(default_factory=set)
    max_hours_weekly: int = 20
    unavailable_slots: Set[int] = field(default_factory=set)
    preferred_slots: Set[int] = field(default_factory=set)
    scheduled_hours: int = 0

@dataclass
class Room:
    room_id: str
    name: str
    type: RoomType
    lab_type: str = ""
    capacity: int = 0
    equipment: List[str] = field(default_factory=list)

@dataclass
class StudentGroup:
    group_id: str
    year: int = 1
    major: str = "general"
    sections: List[str] = field(default_factory=list)
    size: int = 0

@dataclass
class Section:
    section_id: str
    group_id: str
    year: int = 1
    student_count: int = 0
    assigned_courses: List[str] = field(default_factory=list)

@dataclass
class TimetableSlot:
    course_id: str = ""
    component_id: str = ""
    type: str = ""
    room_id: str = ""
    instructor_id: str = ""
    duration: int = 0
    is_taken: bool = False
    is_continuation: bool = False
    student_count: int = 0

@dataclass
class SolverResult:
    success: bool = False
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    message: str = ""

    def add_warning(self, warning: str):
        self.warnings.append(warning)

    def add_error(self, error: str):
        self.errors.append(error)

    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

# ==================== TIMETABLE SOLVER ====================
class TimetableSolver:
    SLOTS_MAX = 40
    PREFERRED_SLOTS = list(range(10, 30))
    UNDESIRABLE_SLOTS = [0, 1, 2, 3, 4, 5, 36, 37, 38, 39]

    def __init__(self):
        self.courses: List[Course] = []
        self.instructors: List[Instructor] = []
        self.rooms: List[Room] = []
        self.sections: List[Section] = []
        self.groups: List[StudentGroup] = []

        # Lookup maps
        self.course_map: Dict[str, Course] = {}
        self.instructor_map: Dict[str, Instructor] = {}
        self.room_map: Dict[str, Room] = {}
        self.section_map: Dict[str, Section] = {}
        self.section_index_map: Dict[str, int] = {}
        self.group_sections_map: Dict[str, List[str]] = {}

        # Timetable structure: slots[sections]
        self.timetable: List[List[TimetableSlot]] = []
        self.scheduled_components: List[Set[str]] = []

        # Availability tracking
        self.instructor_busy: List[Set[str]] = [set() for _ in range(self.SLOTS_MAX)]
        self.room_busy: List[Set[str]] = [set() for _ in range(self.SLOTS_MAX)]
        self.section_busy: List[Set[int]] = [set() for _ in range(self.SLOTS_MAX)]

        self.result = SolverResult()

    def generate_timetable(self, courses: List[Course], instructors: List[Instructor],
                          rooms: List[Room], groups: List[StudentGroup], sections: List[Section]) -> SolverResult:
        """Main method to generate timetable"""
        start_time = time.time()
        self.result = SolverResult()

        try:
            # Validate input
            if not self._validate_input(courses, instructors, rooms, groups, sections):
                self.result.success = False
                return self.result

            # Initialize data structures
            self._parse_input_data(courses, instructors, rooms, groups, sections)

            # Check basic solvability
            if not self._check_solvability():
                self.result.success = False
                self.result.message = "Problem not solvable with current constraints"
                return self.result

            # Multi-phase scheduling
            print("Phase 1: Scheduling Lectures...")
            lecture_count = self._schedule_lectures()

            print("Phase 2: Scheduling Labs...")
            lab_count = self._schedule_labs()

            print("Phase 3: Scheduling Tutorials...")
            tutorial_count = self._schedule_tutorials()

            print("Phase 4: Optimizing...")
            self._optimize_schedule()

            # Generate statistics
            total_components = sum(len(course.components) for course in self.courses)
            scheduled_components = sum(1 for course in self.courses
                                     for comp in course.components if comp.is_scheduled)

            self.result.success = True
            self.result.message = (
                f"Timetable generated successfully. "
                f"Scheduled: {lecture_count} lectures, {lab_count} labs, {tutorial_count} tutorials. "
                f"Completion: {scheduled_components}/{total_components} components"
            )

            execution_time = time.time() - start_time
            print(f"Execution time: {execution_time:.2f} seconds")

        except Exception as e:
            self.result.success = False
            self.result.add_error(f"Unexpected error: {str(e)}")
            import traceback
            traceback.print_exc()

        return self.result

    def _validate_input(self, courses, instructors, rooms, groups, sections) -> bool:
        """Validate input data"""
        if not courses:
            self.result.add_error("No courses provided")
            return False
        if not instructors:
            self.result.add_error("No instructors provided")
            return False
        if not rooms:
            self.result.add_error("No rooms provided")
            return False
        if not sections:
            self.result.add_error("No sections provided")
            return False

        # Additional validation
        for course in courses:
            if not course.components:
                self.result.add_warning(f"Course {course.course_id} has no components")

        return True

    def _parse_input_data(self, courses, instructors, rooms, groups, sections):
        """Parse and initialize input data"""
        self.courses = courses
        self.instructors = instructors
        self.rooms = rooms
        self.groups = groups
        self.sections = sections

        # Build lookup maps
        self.course_map = {course.course_id: course for course in self.courses}
        self.instructor_map = {instr.instructor_id: instr for instr in self.instructors}
        self.room_map = {room.room_id: room for room in self.rooms}
        self.section_map = {section.section_id: section for section in self.sections}
        self.section_index_map = {section.section_id: idx for idx, section in enumerate(self.sections)}

        # Build group-section mappings
        for group in self.groups:
            self.group_sections_map[group.group_id] = group.sections

        # Initialize timetable
        self._initialize_timetable()

    def _initialize_timetable(self):
        """Initialize timetable data structures"""
        self.timetable = [
            [TimetableSlot() for _ in range(len(self.sections))]
            for _ in range(self.SLOTS_MAX)
        ]
        self.scheduled_components = [set() for _ in range(len(self.sections))]

        # Reset availability tracking
        self.instructor_busy = [set() for _ in range(self.SLOTS_MAX)]
        self.room_busy = [set() for _ in range(self.SLOTS_MAX)]
        self.section_busy = [set() for _ in range(self.SLOTS_MAX)]

        # Reset instructor hours and component status
        for instructor in self.instructors:
            instructor.scheduled_hours = 0
        for course in self.courses:
            for component in course.components:
                component.is_scheduled = False

    def _check_solvability(self) -> bool:
        """Check if the problem is solvable"""
        # Check room capacities and types
        has_lecture_rooms = any(room.type == RoomType.LECTURE for room in self.rooms)
        has_lab_rooms = any(room.type == RoomType.LAB for room in self.rooms)
        has_classrooms = any(room.type == RoomType.CLASSROOM for room in self.rooms)

        if not has_lecture_rooms:
            self.result.add_warning("No lecture rooms available")
        if not has_lab_rooms:
            self.result.add_warning("No lab rooms available")
        if not has_classrooms:
            self.result.add_warning("No classrooms available")

        # Check instructor qualifications
        required_qualifications = set()
        for course in self.courses:
            for component in course.components:
                required_qualifications.add(component.instructor_qualification)

        available_qualifications = set()
        for instructor in self.instructors:
            available_qualifications.update(instructor.qualifications)

        missing_qualifications = required_qualifications - available_qualifications
        for qual in missing_qualifications:
            self.result.add_warning(f"No instructors qualified for: {qual}")

        return len(self.result.errors) == 0

    def _schedule_lectures(self) -> int:
        """Schedule lecture components"""
        scheduled_count = 0

        # Collect all unscheduled lectures
        lectures = []
        for course in self.courses:
            for component in course.components:
                if (component.type == ComponentType.LECTURE and
                    not component.is_scheduled):
                    lectures.append((course, component))

        # Sort by difficulty (largest capacity first)
        lectures.sort(key=lambda x: x[1].min_capacity, reverse=True)

        for course, component in lectures:
            target_sections = self._get_target_sections(course, component)

            if not target_sections:
                self.result.add_warning(f"No target sections found for {course.course_id} lecture")
                continue

            qualified_instructors = self._get_qualified_instructors(
                component.instructor_qualification, ComponentType.LECTURE
            )
            suitable_rooms = self._get_suitable_rooms(
                "lecture", "", component.min_capacity
            )

            if not qualified_instructors:
                self.result.add_warning(f"No qualified professors found for {course.course_id} lecture")
                continue
            if not suitable_rooms:
                self.result.add_warning(f"No suitable rooms found for {course.course_id} lecture")
                continue

            # Try to schedule (preferred middle slots first)
            scheduled = False
            for slot in self.PREFERRED_SLOTS:
                for instructor_id in qualified_instructors:
                    for room_id in suitable_rooms:
                        if self._is_valid_assignment(target_sections, slot, component.duration_slots, instructor_id, room_id):
                            if self._place_assignment(target_sections, course.course_id, component.component_id,
                                                    "lecture", component.duration_slots, instructor_id, room_id, slot):
                                scheduled = True
                                scheduled_count += 1
                                print(f"  ✓ Scheduled {course.course_id} lecture at slot {slot}")
                                break
                    if scheduled:
                        break
                if scheduled:
                    break

            if not scheduled:
                self.result.add_warning(f"Failed to schedule {course.course_id} lecture - no available time slot")

        print(f"  Scheduled {scheduled_count}/{len(lectures)} lectures")
        return scheduled_count

    def _schedule_labs(self) -> int:
        """Schedule lab components"""
        scheduled_count = 0

        for course in self.courses:
            for component in course.components:
                if (component.type == ComponentType.LAB and
                    not component.is_scheduled):

                    sections_scheduled = 0
                    for section_id in component.student_sections:
                        if section_id not in self.section_index_map:
                            continue

                        section_idx = self.section_index_map[section_id]
                        if component.component_id in self.scheduled_components[section_idx]:
                            continue

                        target_sections = [section_idx]
                        qualified_instructors = self._get_qualified_instructors(
                            component.instructor_qualification, ComponentType.LAB
                        )
                        suitable_rooms = self._get_suitable_rooms(
                            "lab", component.lab_type, component.min_capacity
                        )

                        if not qualified_instructors or not suitable_rooms:
                            continue

                        # Try to schedule
                        scheduled = False
                        for slot in range(self.SLOTS_MAX):
                            for instructor_id in qualified_instructors:
                                for room_id in suitable_rooms:
                                    if self._is_valid_assignment(target_sections, slot, component.duration_slots, instructor_id, room_id):
                                        if self._place_assignment(target_sections, course.course_id, component.component_id,
                                                                "lab", component.duration_slots, instructor_id, room_id, slot):
                                            sections_scheduled += 1
                                            scheduled = True
                                            break
                                if scheduled:
                                    break
                            if scheduled:
                                break

                    if sections_scheduled > 0:
                        scheduled_count += 1
                        component.is_scheduled = True

        print(f"  Scheduled {scheduled_count} labs")
        return scheduled_count

    def _schedule_tutorials(self) -> int:
        """Schedule tutorial components"""
        scheduled_count = 0

        for course in self.courses:
            for component in course.components:
                if (component.type == ComponentType.TUTORIAL and
                    not component.is_scheduled):

                    sections_scheduled = 0
                    for section_id in component.student_sections:
                        if section_id not in self.section_index_map:
                            continue

                        section_idx = self.section_index_map[section_id]
                        if component.component_id in self.scheduled_components[section_idx]:
                            continue

                        target_sections = [section_idx]
                        qualified_instructors = self._get_qualified_instructors(
                            component.instructor_qualification, ComponentType.TUTORIAL
                        )
                        suitable_rooms = self._get_suitable_rooms(
                            "classroom", "", component.min_capacity
                        )

                        if not qualified_instructors or not suitable_rooms:
                            continue

                        # Try to schedule
                        scheduled = False
                        for slot in range(self.SLOTS_MAX):
                            for instructor_id in qualified_instructors:
                                for room_id in suitable_rooms:
                                    if self._is_valid_assignment(target_sections, slot, component.duration_slots, instructor_id, room_id):
                                        if self._place_assignment(target_sections, course.course_id, component.component_id,
                                                                "tutorial", component.duration_slots, instructor_id, room_id, slot):
                                            sections_scheduled += 1
                                            scheduled = True
                                            break
                                if scheduled:
                                    break
                            if scheduled:
                                break

                    if sections_scheduled > 0:
                        scheduled_count += 1
                        component.is_scheduled = True

        print(f"  Scheduled {scheduled_count} tutorials")
        return scheduled_count

    def _optimize_schedule(self):
        """Optimize schedule using local search"""
        improvements = 0

        for slot in self.UNDESIRABLE_SLOTS:
            for section_idx in range(len(self.sections)):
                if slot >= len(self.timetable) or section_idx >= len(self.timetable[slot]):
                    continue

                assignment = self.timetable[slot][section_idx]
                if (not assignment.is_taken or assignment.is_continuation or
                    not assignment.course_id):
                    continue

                # Try to find a better slot
                for new_slot in self.PREFERRED_SLOTS:
                    if self._can_move_assignment(section_idx, slot, new_slot, assignment):
                        self._move_assignment(section_idx, slot, new_slot, assignment)
                        improvements += 1
                        break

        print(f"  Made {improvements} improvements during optimization")

    def _can_move_assignment(self, section_idx: int, old_slot: int, new_slot: int,
                           assignment: TimetableSlot) -> bool:
        """Check if an assignment can be moved"""
        if new_slot + assignment.duration > self.SLOTS_MAX:
            return False

        # Check instructor availability
        for s in range(new_slot, new_slot + assignment.duration):
            if assignment.instructor_id in self.instructor_busy[s]:
                return False

        # Check room availability
        for s in range(new_slot, new_slot + assignment.duration):
            if assignment.room_id in self.room_busy[s]:
                return False

        # Check section availability
        for s in range(new_slot, new_slot + assignment.duration):
            if section_idx in self.section_busy[s]:
                return False

        return True

    def _move_assignment(self, section_idx: int, old_slot: int, new_slot: int,
                        assignment: TimetableSlot):
        """Move an assignment to a new slot"""
        # Remove old assignment
        for s in range(old_slot, old_slot + assignment.duration):
            if s < self.SLOTS_MAX and section_idx < len(self.timetable[s]):
                self.timetable[s][section_idx] = TimetableSlot()
                self.instructor_busy[s].discard(assignment.instructor_id)
                self.room_busy[s].discard(assignment.room_id)
                self.section_busy[s].discard(section_idx)

        # Create new assignment
        for offset in range(assignment.duration):
            slot_idx = new_slot + offset
            if slot_idx < self.SLOTS_MAX and section_idx < len(self.timetable[slot_idx]):
                new_assignment = TimetableSlot(
                    course_id=assignment.course_id,
                    component_id=assignment.component_id,
                    type=assignment.type,
                    room_id=assignment.room_id,
                    instructor_id=assignment.instructor_id,
                    duration=assignment.duration,
                    is_taken=True,
                    is_continuation=(offset > 0),
                    student_count=assignment.student_count
                )
                self.timetable[slot_idx][section_idx] = new_assignment
                self.instructor_busy[slot_idx].add(assignment.instructor_id)
                self.room_busy[slot_idx].add(assignment.room_id)
                self.section_busy[slot_idx].add(section_idx)

    # ==================== HELPER METHODS ====================
    def _get_target_sections(self, course: Course, component: CourseComponent) -> List[int]:
        """Get target section indices for a component"""
        target_sections = []

        if component.type == ComponentType.LECTURE:
            for group_id in component.student_groups:
                if group_id in self.group_sections_map:
                    for section_id in self.group_sections_map[group_id]:
                        if section_id in self.section_index_map:
                            section_idx = self.section_index_map[section_id]
                            # Check if section needs this course
                            section = self.section_map[section_id]
                            if (component.component_id not in self.scheduled_components[section_idx] and
                                course.course_id in section.assigned_courses):
                                target_sections.append(section_idx)
        else:
            for section_id in component.student_sections:
                if section_id in self.section_index_map:
                    section_idx = self.section_index_map[section_id]
                    if component.component_id not in self.scheduled_components[section_idx]:
                        target_sections.append(section_idx)

        return list(set(target_sections))  # Remove duplicates

    def _get_qualified_instructors(self, qualification: str, component_type: ComponentType) -> List[str]:
        """Get instructors qualified for a component"""
        qualified = []
        for instructor in self.instructors:
            if qualification in instructor.qualifications:
                if component_type == ComponentType.LECTURE:
                    if instructor.type == InstructorType.PROFESSOR:
                        qualified.append(instructor.instructor_id)
                else:  # lab or tutorial
                    if instructor.type in [InstructorType.TA, InstructorType.PART_TIME]:
                        qualified.append(instructor.instructor_id)

        # Sort by current load (least busy first)
        qualified.sort(key=lambda instr_id: self.instructor_map[instr_id].scheduled_hours)
        return qualified

    def _get_suitable_rooms(self, room_type: str, lab_type: str, min_capacity: int) -> List[str]:
        """Get suitable rooms for a component"""
        suitable = []
        for room in self.rooms:
            if (room.type.value == room_type and
                room.capacity >= min_capacity):
                if room_type == "lab" and lab_type:
                    if room.lab_type == lab_type:
                        suitable.append(room.room_id)
                else:
                    suitable.append(room.room_id)

        # Sort by capacity (smallest suitable first)
        suitable.sort(key=lambda room_id: self.room_map[room_id].capacity)
        return suitable

    def _is_valid_assignment(self, target_sections: List[int], slot: int, duration: int,
                           instructor_id: str, room_id: str) -> bool:
        """Check if an assignment is valid"""
        if slot < 0 or slot >= self.SLOTS_MAX:
            return False
        if slot + duration > self.SLOTS_MAX:
            return False

        # Check instructor availability
        for s in range(slot, slot + duration):
            if instructor_id in self.instructor_busy[s]:
                return False

        # Check room availability
        for s in range(slot, slot + duration):
            if room_id in self.room_busy[s]:
                return False

        # Check section availability
        for section_idx in target_sections:
            for s in range(slot, slot + duration):
                if section_idx in self.section_busy[s]:
                    return False

        return True

    def _place_assignment(self, target_sections: List[int], course_id: str, component_id: str,
                         component_type: str, duration: int, instructor_id: str,
                         room_id: str, slot: int) -> bool:
        """Place an assignment in the timetable"""
        try:
            for section_idx in target_sections:
                # Create main assignment
                if section_idx < len(self.timetable[slot]):
                    self.timetable[slot][section_idx] = TimetableSlot(
                        course_id=course_id,
                        component_id=component_id,
                        type=component_type,
                        room_id=room_id,
                        instructor_id=instructor_id,
                        duration=duration,
                        is_taken=True,
                        is_continuation=False,
                        student_count=self.sections[section_idx].student_count
                    )

                # Create continuation slots
                for offset in range(1, duration):
                    continuation_slot = slot + offset
                    if continuation_slot < self.SLOTS_MAX and section_idx < len(self.timetable[continuation_slot]):
                        self.timetable[continuation_slot][section_idx] = TimetableSlot(
                            course_id=course_id,
                            component_id=component_id,
                            type=component_type,
                            room_id=room_id,
                            instructor_id=instructor_id,
                            duration=duration,
                            is_taken=True,
                            is_continuation=True,
                            student_count=self.sections[section_idx].student_count
                        )

                # Update section tracking
                self.scheduled_components[section_idx].add(component_id)

            # Update availability tracking
            for s in range(slot, slot + duration):
                if s < self.SLOTS_MAX:
                    self.instructor_busy[s].add(instructor_id)
                    self.room_busy[s].add(room_id)
                    for section_idx in target_sections:
                        self.section_busy[s].add(section_idx)

            # Update instructor hours
            if instructor_id in self.instructor_map:
                self.instructor_map[instructor_id].scheduled_hours += duration

            # Mark component as scheduled - FIXED: Find the correct component and mark it
            for course in self.courses:
                for component in course.components:
                    if component.component_id == component_id:
                        component.is_scheduled = True
                        break

            return True

        except Exception as e:
            print(f"Error placing assignment: {e}")
            return False

    # ==================== GETTER METHODS ====================
    def get_timetable(self):
        return self.timetable

    def get_sections(self):
        return self.sections

    def get_courses(self):
        return self.courses

    def get_result(self):
        return self.result

# ==================== JSON HANDLER ====================
class JsonHandler:
    @staticmethod
    def parse_input(json_data: dict) -> Tuple[List[Course], List[Instructor], List[Room],
                                            List[StudentGroup], List[Section], List[str]]:
        """Parse JSON input into data models"""
        errors = []
        courses = []
        instructors = []
        rooms = []
        groups = []
        sections = []

        try:
            # Parse courses
            if 'courses' in json_data:
                for course_data in json_data['courses']:
                    try:
                        course = Course(
                            course_id=course_data.get('courseID', ''),
                            course_name=course_data.get('courseName', ''),
                            course_type=course_data.get('courseType', 'core'),
                            all_year=course_data.get('allYear', False)
                        )

                        if 'components' in course_data:
                            for comp_data in course_data['components']:
                                component = CourseComponent(
                                    component_id=comp_data.get('componentID', ''),
                                    type=ComponentType(comp_data.get('type', 'lecture')),
                                    lab_type=comp_data.get('labType', ''),
                                    duration_slots=comp_data.get('durationSlots', 1),
                                    min_capacity=comp_data.get('minCapacity', 0),
                                    instructor_qualification=comp_data.get('instructorQualification', ''),
                                    requires_lecture_first=comp_data.get('requiresLectureFirst', False),
                                    concurrent_sections=comp_data.get('concurrentSections', False),
                                    student_groups=comp_data.get('studentGroups', []),
                                    student_sections=comp_data.get('studentSections', [])
                                )
                                course.components.append(component)

                        courses.append(course)
                    except Exception as e:
                        errors.append(f"Error parsing course {course_data.get('courseID', 'unknown')}: {str(e)}")

            # Parse instructors
            if 'instructors' in json_data:
                for instr_data in json_data['instructors']:
                    try:
                        instructor = Instructor(
                            instructor_id=instr_data.get('instructorID', ''),
                            name=instr_data.get('name', ''),
                            type=InstructorType(instr_data.get('type', 'professor')),
                            max_hours_weekly=instr_data.get('maxHoursWeekly', 20),
                            qualifications=set(instr_data.get('qualifications', [])),
                            unavailable_slots=set(instr_data.get('unavailableSlots', [])),
                            preferred_slots=set(instr_data.get('preferredSlots', []))
                        )
                        instructors.append(instructor)
                    except Exception as e:
                        errors.append(f"Error parsing instructor {instr_data.get('instructorID', 'unknown')}: {str(e)}")

            # Parse rooms
            if 'rooms' in json_data:
                for room_data in json_data['rooms']:
                    try:
                        room = Room(
                            room_id=room_data.get('roomID', ''),
                            name=room_data.get('name', ''),
                            type=RoomType(room_data.get('type', 'classroom')),
                            lab_type=room_data.get('labType', ''),
                            capacity=room_data.get('capacity', 0),
                            equipment=room_data.get('equipment', [])
                        )
                        rooms.append(room)
                    except Exception as e:
                        errors.append(f"Error parsing room {room_data.get('roomID', 'unknown')}: {str(e)}")

            # Parse student groups
            if 'studentGroups' in json_data:
                for group_data in json_data['studentGroups']:
                    try:
                        group = StudentGroup(
                            group_id=group_data.get('groupID', ''),
                            year=group_data.get('year', 1),
                            major=group_data.get('major', 'general'),
                            size=group_data.get('size', 0),
                            sections=group_data.get('sections', [])
                        )
                        groups.append(group)
                    except Exception as e:
                        errors.append(f"Error parsing group {group_data.get('groupID', 'unknown')}: {str(e)}")

            # Parse sections
            if 'sections' in json_data:
                for section_data in json_data['sections']:
                    try:
                        section = Section(
                            section_id=section_data.get('sectionID', ''),
                            group_id=section_data.get('groupID', ''),
                            year=section_data.get('year', 1),
                            student_count=section_data.get('studentCount', 0),
                            assigned_courses=section_data.get('assignedCourses', [])
                        )
                        sections.append(section)
                    except Exception as e:
                        errors.append(f"Error parsing section {section_data.get('sectionID', 'unknown')}: {str(e)}")

        except Exception as e:
            errors.append(f"General parsing error: {str(e)}")

        return courses, instructors, rooms, groups, sections, errors

    @staticmethod
    def create_response(solver: TimetableSolver) -> dict:
        """Create JSON response from solver results"""
        result = solver.get_result()
        timetable = solver.get_timetable()
        sections = solver.get_sections()
        courses = solver.get_courses()

        response = {
            'success': result.success,
            'message': result.message
        }

        if result.warnings:
            response['warnings'] = result.warnings
        if result.errors:
            response['errors'] = result.errors

        if result.success:
            # Build sections schedule
            sections_json = []
            days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday']
            start_times = ['09:00', '09:45', '10:45', '11:30', '12:30', '13:15', '14:15', '15:00']
            end_times = ['09:45', '10:30', '11:30', '12:15', '13:15', '14:00', '15:00', '15:45']

            for j, section in enumerate(sections):
                section_data = {
                    'sectionID': section.section_id,
                    'groupID': section.group_id,
                    'year': section.year,
                    'studentCount': section.student_count,
                    'schedule': []
                }

                for i in range(len(timetable)):
                    if (i < len(timetable) and j < len(timetable[i]) and
                        timetable[i][j].is_taken and not timetable[i][j].is_continuation):
                        slot_data = {
                            'slotIndex': i,
                            'courseID': timetable[i][j].course_id,
                            'componentID': timetable[i][j].component_id,
                            'type': timetable[i][j].type,
                            'roomID': timetable[i][j].room_id,
                            'instructorID': timetable[i][j].instructor_id,
                            'duration': timetable[i][j].duration,
                            'studentCount': timetable[i][j].student_count
                        }

                        # Add time information
                        day_idx = i // 8
                        period = i % 8
                        if day_idx < len(days) and period < len(start_times):
                            slot_data.update({
                                'day': days[day_idx],
                                'period': period + 1,
                                'startTime': start_times[period],
                                'endTime': end_times[period + timetable[i][j].duration - 1]
                            })

                        section_data['schedule'].append(slot_data)

                sections_json.append(section_data)

            response['sections'] = sections_json

            # Add statistics
            total_components = sum(len(course.components) for course in courses)
            scheduled_components = sum(1 for course in courses
                                     for comp in course.components if comp.is_scheduled)

            response['statistics'] = {
                'totalComponents': total_components,
                'scheduledComponents': scheduled_components,
                'completionRate': f"{scheduled_components}/{total_components}"
            }

        return response

# ==================== FLASK SERVER ====================
app = Flask(__name__)
CORS(app)

@app.route('/api/schedule', methods=['POST'])
def schedule_timetable():
    """Main scheduling endpoint"""
    try:
        if not request.json:
            return jsonify({
                'success': False,
                'error': 'Empty request body'
            }), 400

        # Parse input data
        courses, instructors, rooms, groups, sections, parse_errors = JsonHandler.parse_input(request.json)

        if parse_errors:
            return jsonify({
                'success': False,
                'error': 'Invalid input data',
                'parseErrors': parse_errors
            }), 400

        # Generate timetable
        solver = TimetableSolver()
        result = solver.generate_timetable(courses, instructors, rooms, groups, sections)

        # Create response
        response = JsonHandler.create_response(solver)

        return jsonify(response), 200 if result.success else 400

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Processing error: {str(e)}'
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'timetable_scheduler',
        'timestamp': time.time()
    })

if __name__ == '__main__':
    print("=" * 50)
    print("    University Timetable Generator Server")
    print("=" * 50)
    print("Server running on: http://localhost:8080")
    print("Endpoints:")
    print("  POST /api/schedule - Generate timetable")
    print("  GET  /health       - Health check")
    print("=" * 50)

    app.run(host='0.0.0.0', port=8080, debug=False)