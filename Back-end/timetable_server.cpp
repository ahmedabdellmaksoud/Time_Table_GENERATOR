#include <httplib.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <vector>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <algorithm>
#include <memory>
#include <stdexcept>

// Remove using namespace for safety
using json = nlohmann::json;

// ==================== DATA MODELS ====================
struct CourseComponent {
    std::string component_id;
    std::string type;  // "lecture", "lab", "tutorial"
    std::string lab_type;
    int duration_slots{1};
    int min_capacity{0};
    std::string instructor_qualification;
    bool requires_lecture_first{false};
    bool concurrent_sections{false};
    std::vector<std::string> student_groups;
    std::vector<std::string> student_sections;
    bool is_scheduled{false};
};

struct Course {
    std::string course_id;
    std::string course_name;
    std::string course_type;
    std::vector<CourseComponent> components;
    bool all_year{false};
};

struct Instructor {
    std::string instructor_id;
    std::string name;
    std::string type;  // "professor", "ta", "part_time"
    std::unordered_set<std::string> qualifications;
    int max_hours_weekly{20};
    std::unordered_set<int> unavailable_slots;
    std::unordered_set<int> preferred_slots;
    int scheduled_hours{0};
};

struct Room {
    std::string room_id;
    std::string name;
    std::string type;  // "lecture", "lab", "classroom"
    std::string lab_type;
    int capacity{0};
    std::vector<std::string> equipment;
};

struct StudentGroup {
    std::string group_id;
    int year{1};
    std::string major;
    std::vector<std::string> sections;
    int size{0};
};

struct Section {
    std::string section_id;
    std::string group_id;
    int year{1};
    int student_count{0};
    std::vector<std::string> assigned_courses;
};

struct TimetableSlot {
    std::string course_id;
    std::string component_id;
    std::string type;
    std::string room_id;
    std::string instructor_id;
    int duration{0};
    bool is_taken{false};
    bool is_continuation{false};
    int student_count{0};

    TimetableSlot() = default;
};

// ==================== ERROR AND WARNING SYSTEM ====================
class SolverError : public std::runtime_error {
public:
    explicit SolverError(const std::string& message) : std::runtime_error(message) {}
};

class ValidationError : public SolverError {
public:
    explicit ValidationError(const std::string& message) : SolverError("Validation Error: " + message) {}
};

class ResourceError : public SolverError {
public:
    explicit ResourceError(const std::string& message) : SolverError("Resource Error: " + message) {}
};

struct SolverResult {
    bool success{false};
    std::vector<std::string> warnings;
    std::vector<std::string> errors;
    std::string message;

    void add_warning(const std::string& warning) {
        warnings.push_back(warning);
    }

    void add_error(const std::string& error) {
        errors.push_back(error);
    }

    bool has_errors() const { return !errors.empty(); }
    bool has_warnings() const { return !warnings.empty(); }
};

// ==================== TIMETABLE SOLVER ====================
class TimetableSolver {
public:
    static const int SLOTS_MAX = 40;

private:

    // Data storage
    std::vector<Course> courses_;
    std::vector<Instructor> instructors_;
    std::vector<Room> rooms_;
    std::vector<Section> sections_;
    std::vector<StudentGroup> groups_;

    // Timetable structure
    std::vector<std::vector<TimetableSlot>> timetable_;
    int sections_max_{0};

    // Tracking structures
    std::unordered_map<std::string, int> section_to_index_;
    std::unordered_map<std::string, std::string> section_to_group_;
    std::unordered_map<std::string, std::vector<std::string>> group_to_sections_;
    std::unordered_map<int, std::vector<std::string>> year_to_sections_;
    std::vector<std::unordered_set<std::string>> scheduled_components_;

    // Availability tracking
    std::unordered_set<std::string> instructor_busy_[SLOTS_MAX];
    std::unordered_set<std::string> room_busy_[SLOTS_MAX];

    // Lookup maps
    std::unordered_map<std::string, Course> course_map_;
    std::unordered_map<std::string, Instructor> instructor_map_;
    std::unordered_map<std::string, Room> room_map_;

    // Result tracking
    SolverResult result_;

public:
    TimetableSolver() = default;

    const SolverResult& get_result() const { return result_; }
    const std::vector<std::vector<TimetableSlot>>& get_timetable() const { return timetable_; }
    const std::vector<Section>& get_sections() const { return sections_; }
    const std::vector<Course>& get_courses() const { return courses_; }

    // Main scheduling method
    SolverResult generate_timetable(const std::vector<Course>& courses,
                                  const std::vector<Instructor>& instructors,
                                  const std::vector<Room>& rooms,
                                  const std::vector<StudentGroup>& groups,
                                  const std::vector<Section>& sections) {
        result_ = SolverResult();

        try {
            // Validate input data first
            if (!validate_input_data(courses, instructors, rooms, groups, sections)) {
                result_.success = false;
                return result_;
            }

            // Check solvability
            if (!check_solvability()) {
                result_.success = false;
                result_.message = "Problem is not solvable with current constraints";
                return result_;
            }

            // Parse and initialize data
            parse_input_data(courses, instructors, rooms, groups, sections);

            // Initialize timetable structures
            initialize_timetable();

            // Multi-phase scheduling
            if (!schedule_lectures()) {
                throw SolverError("Failed to schedule lectures");
            }
            if (!schedule_labs()) {
                throw SolverError("Failed to schedule labs");
            }
            if (!schedule_tutorials()) {
                throw SolverError("Failed to schedule tutorials");
            }

            // Optimization
            optimize_schedule();

            result_.success = true;
            result_.message = "Timetable generated successfully";

        } catch (const SolverError& e) {
            result_.success = false;
            result_.add_error(e.what());
        } catch (const std::exception& e) {
            result_.success = false;
            result_.add_error(std::string("Unexpected error: ") + e.what());
        }

        return result_;
    }

private:
    // ==================== VALIDATION METHODS ====================
    bool validate_input_data(const std::vector<Course>& courses,
                           const std::vector<Instructor>& instructors,
                           const std::vector<Room>& rooms,
                           const std::vector<StudentGroup>& groups,
                           const std::vector<Section>& sections) {
        bool valid = true;

        // Check for empty data
        if (courses.empty()) {
            result_.add_error("No courses provided");
            valid = false;
        }
        if (instructors.empty()) {
            result_.add_error("No instructors provided");
            valid = false;
        }
        if (rooms.empty()) {
            result_.add_error("No rooms provided");
            valid = false;
        }
        if (sections.empty()) {
            result_.add_error("No sections provided");
            valid = false;
        }

        // Validate courses have components
        for (const auto& course : courses) {
            if (course.components.empty()) {
                result_.add_warning("Course " + course.course_id + " has no components");
            }

            for (const auto& component : course.components) {
                if (component.type == "lecture" && component.student_groups.empty()) {
                    result_.add_warning("Lecture component " + component.component_id + " has no student groups");
                }
                if ((component.type == "lab" || component.type == "tutorial") && component.student_sections.empty()) {
                    result_.add_warning(component.type + " component " + component.component_id + " has no student sections");
                }
            }
        }

        // Validate section-group relationships
        for (const auto& section : sections) {
            bool group_found = false;
            for (const auto& group : groups) {
                if (std::find(group.sections.begin(), group.sections.end(), section.section_id) != group.sections.end()) {
                    group_found = true;
                    break;
                }
            }
            if (!group_found) {
                result_.add_warning("Section " + section.section_id + " is not assigned to any group");
            }
        }

        return valid && !result_.has_errors();
    }

    bool check_solvability() {
        // Check if there are enough resources
        int total_lecture_capacity = 0;
        int total_lab_capacity = 0;
        int total_classroom_capacity = 0;

        for (const auto& room : rooms_) {
            if (room.type == "lecture") total_lecture_capacity += room.capacity;
            else if (room.type == "lab") total_lab_capacity += room.capacity;
            else if (room.type == "classroom") total_classroom_capacity += room.capacity;
        }

        // Check instructor qualifications
        std::unordered_set<std::string> required_qualifications;
        for (const auto& course : courses_) {
            for (const auto& component : course.components) {
                required_qualifications.insert(component.instructor_qualification);
            }
        }

        std::unordered_set<std::string> available_qualifications;
        for (const auto& instructor : instructors_) {
            available_qualifications.insert(instructor.qualifications.begin(), instructor.qualifications.end());
        }

        for (const auto& qual : required_qualifications) {
            if (available_qualifications.find(qual) == available_qualifications.end()) {
                result_.add_warning("No instructors qualified for: " + qual);
            }
        }

        // Check room type availability
        bool has_lecture_rooms = false;
        bool has_lab_rooms = false;
        bool has_classrooms = false;

        for (const auto& room : rooms_) {
            if (room.type == "lecture") has_lecture_rooms = true;
            else if (room.type == "lab") has_lab_rooms = true;
            else if (room.type == "classroom") has_classrooms = true;
        }

        if (!has_lecture_rooms) result_.add_warning("No lecture rooms available");
        if (!has_lab_rooms) result_.add_warning("No lab rooms available");
        if (!has_classrooms) result_.add_warning("No classrooms available");

        return !result_.has_errors();
    }

    // ==================== SCHEDULING METHODS ====================
    void parse_input_data(const std::vector<Course>& courses,
                         const std::vector<Instructor>& instructors,
                         const std::vector<Room>& rooms,
                         const std::vector<StudentGroup>& groups,
                         const std::vector<Section>& sections) {
        courses_ = courses;
        instructors_ = instructors;
        rooms_ = rooms;
        groups_ = groups;
        sections_ = sections;

        // Build lookup maps
        for (const auto& course : courses_) {
            course_map_[course.course_id] = course;
        }
        for (const auto& instructor : instructors_) {
            instructor_map_[instructor.instructor_id] = instructor;
        }
        for (const auto& room : rooms_) {
            room_map_[room.room_id] = room;
        }

        // Build section indices and group mappings
        int idx = 0;
        for (const auto& section : sections_) {
            section_to_index_[section.section_id] = idx;
            section_to_group_[section.section_id] = section.group_id;
            year_to_sections_[section.year].push_back(section.section_id);
            idx++;
        }

        // Build group to sections mapping
        for (const auto& group : groups_) {
            for (const auto& section_id : group.sections) {
                group_to_sections_[group.group_id].push_back(section_id);
            }
        }

        sections_max_ = sections_.size();
    }

    void initialize_timetable() {
        timetable_.clear();
        timetable_.resize(SLOTS_MAX, std::vector<TimetableSlot>(sections_max_));
        scheduled_components_.clear();
        scheduled_components_.resize(sections_max_);

        // Clear availability tracking
        for (int i = 0; i < SLOTS_MAX; ++i) {
            instructor_busy_[i].clear();
            room_busy_[i].clear();
        }

        // Reset instructor hours
        for (auto& instructor : instructors_) {
            instructor.scheduled_hours = 0;
        }

        // Reset component scheduling status
        for (auto& course : courses_) {
            for (auto& component : course.components) {
                component.is_scheduled = false;
            }
        }
    }

    bool schedule_lectures() {
        std::cout << "Phase 1: Scheduling Lectures..." << std::endl;

        // Collect all unscheduled lectures
        std::vector<std::pair<Course*, CourseComponent*>> lectures;
        for (auto& course : courses_) {
            for (auto& component : course.components) {
                if (component.type == "lecture" && !component.is_scheduled) {
                    lectures.push_back({&course, &component});
                }
            }
        }

        // Sort by difficulty (largest capacity first)
        std::sort(lectures.begin(), lectures.end(),
            [](const auto& a, const auto& b) {
                return a.second->min_capacity > b.second->min_capacity;
            });

        int scheduled_count = 0;
        for (auto& lecture : lectures) {
            Course* course = lecture.first;
            CourseComponent* component = lecture.second;

            if (component->is_scheduled) continue;

            // Find target sections for this lecture
            std::vector<int> target_sections;
            for (const auto& group_id : component->student_groups) {
                auto group_it = group_to_sections_.find(group_id);
                if (group_it != group_to_sections_.end()) {
                    for (const auto& section_id : group_it->second) {
                        auto section_it = section_to_index_.find(section_id);
                        if (section_it != section_to_index_.end()) {
                            int sec_idx = section_it->second;
                            // Check if section needs this course and hasn't been scheduled
                            const auto& assigned = sections_[sec_idx].assigned_courses;
                            if (std::find(assigned.begin(), assigned.end(), course->course_id) != assigned.end() &&
                                scheduled_components_[sec_idx].find(component->component_id) == scheduled_components_[sec_idx].end()) {
                                target_sections.push_back(sec_idx);
                            }
                        }
                    }
                }
            }

            if (target_sections.empty()) {
                result_.add_warning("No target sections found for " + course->course_id + " lecture");
                continue;
            }

            // Get qualified instructors (professors only for lectures)
            auto qualified_instructors = get_qualified_instructors(component->instructor_qualification, "lecture");
            auto suitable_rooms = get_suitable_rooms("lecture", "", component->min_capacity);

            if (qualified_instructors.empty()) {
                result_.add_warning("No qualified professors found for " + course->course_id + " lecture");
                continue;
            }
            if (suitable_rooms.empty()) {
                result_.add_warning("No suitable rooms found for " + course->course_id + " lecture (need capacity: " +
                                   std::to_string(component->min_capacity) + ")");
                continue;
            }

            // Try to schedule (preferred middle slots first)
            bool scheduled = false;
            for (int slot = 10; slot < 30 && !scheduled; ++slot) {
                for (const auto& instructor_id : qualified_instructors) {
                    for (const auto& room_id : suitable_rooms) {
                        if (is_valid_assignment(target_sections, slot, component->duration_slots, instructor_id, room_id)) {
                            place_assignment(target_sections, course->course_id, component->component_id,
                                           "lecture", component->duration_slots, instructor_id, room_id, slot);
                            scheduled = true;
                            scheduled_count++;
                            std::cout << "  ✓ Scheduled " << course->course_id << " lecture at slot " << slot << std::endl;
                            break;
                        }
                    }
                    if (scheduled) break;
                }
            }

            if (!scheduled) {
                result_.add_warning("Failed to schedule " + course->course_id + " lecture - no available time slot");
            }
        }

        std::cout << "  Scheduled " << scheduled_count << "/" << lectures.size() << " lectures" << std::endl;
        return scheduled_count > 0;  // Consider success if at least one lecture scheduled
    }

    bool schedule_labs() {
        std::cout << "Phase 2: Scheduling Labs..." << std::endl;
        // Simplified implementation - similar structure to lectures but for individual sections
        int scheduled_count = 0;

        for (auto& course : courses_) {
            for (auto& component : course.components) {
                if (component.type == "lab" && !component.is_scheduled) {
                    for (const auto& section_id : component.student_sections) {
                        auto section_it = section_to_index_.find(section_id);
                        if (section_it == section_to_index_.end()) {
                            result_.add_warning("Section " + section_id + " not found for lab " + component.component_id);
                            continue;
                        }

                        int sec_idx = section_it->second;
                        if (scheduled_components_[sec_idx].find(component.component_id) != scheduled_components_[sec_idx].end()) {
                            continue;  // Already scheduled
                        }

                        std::vector<int> target_sections = {sec_idx};
                        auto qualified_instructors = get_qualified_instructors(component.instructor_qualification, "lab");
                        auto suitable_rooms = get_suitable_rooms("lab", component.lab_type, component.min_capacity);

                        if (qualified_instructors.empty() || suitable_rooms.empty()) {
                            continue;
                        }

                        // Try to schedule
                        for (int slot = 0; slot < SLOTS_MAX && !component.is_scheduled; ++slot) {
                            for (const auto& instructor_id : qualified_instructors) {
                                for (const auto& room_id : suitable_rooms) {
                                    if (is_valid_assignment(target_sections, slot, component.duration_slots, instructor_id, room_id)) {
                                        place_assignment(target_sections, course.course_id, component.component_id,
                                                       "lab", component.duration_slots, instructor_id, room_id, slot);
                                        scheduled_count++;
                                        break;
                                    }
                                }
                                if (component.is_scheduled) break;
                            }
                        }
                    }
                }
            }
        }

        std::cout << "  Scheduled " << scheduled_count << " labs" << std::endl;
        return true;
    }

    bool schedule_tutorials() {
        std::cout << "Phase 3: Scheduling Tutorials..." << std::endl;
        // Similar to labs
        int scheduled_count = 0;

        for (auto& course : courses_) {
            for (auto& component : course.components) {
                if (component.type == "tutorial" && !component.is_scheduled) {
                    for (const auto& section_id : component.student_sections) {
                        auto section_it = section_to_index_.find(section_id);
                        if (section_it == section_to_index_.end()) continue;

                        int sec_idx = section_it->second;
                        if (scheduled_components_[sec_idx].find(component.component_id) != scheduled_components_[sec_idx].end()) {
                            continue;
                        }

                        std::vector<int> target_sections = {sec_idx};
                        auto qualified_instructors = get_qualified_instructors(component.instructor_qualification, "tutorial");
                        auto suitable_rooms = get_suitable_rooms("classroom", "", component.min_capacity);

                        if (qualified_instructors.empty() || suitable_rooms.empty()) {
                            continue;
                        }

                        // Try to schedule
                        for (int slot = 0; slot < SLOTS_MAX && !component.is_scheduled; ++slot) {
                            for (const auto& instructor_id : qualified_instructors) {
                                for (const auto& room_id : suitable_rooms) {
                                    if (is_valid_assignment(target_sections, slot, component.duration_slots, instructor_id, room_id)) {
                                        place_assignment(target_sections, course.course_id, component.component_id,
                                                       "tutorial", component.duration_slots, instructor_id, room_id, slot);
                                        scheduled_count++;
                                        break;
                                    }
                                }
                                if (component.is_scheduled) break;
                            }
                        }
                    }
                }
            }
        }

        std::cout << "  Scheduled " << scheduled_count << " tutorials" << std::endl;
        return true;
    }

    void optimize_schedule() {
        std::cout << "Phase 4: Optimizing Schedule..." << std::endl;
        // Simple optimization: move classes from undesirable slots to preferred ones
        std::set<int> undesirable_slots = {0,1,2,3,4,5,36,37,38,39};
        int improvements = 0;

        for (int slot : undesirable_slots) {
            for (int sec_idx = 0; sec_idx < sections_max_; sec_idx++) {
                // Safety checks
                if (slot >= timetable_.size() || sec_idx >= timetable_[slot].size()) continue;

                TimetableSlot& assignment = timetable_[slot][sec_idx];
                if (!assignment.is_taken || assignment.is_continuation || assignment.course_id.empty()) {
                    continue;
                }

                // Try to find a better slot
                for (int new_slot = 10; new_slot < 30; new_slot++) {
                    std::vector<int> target_sections = {sec_idx};

                    if (is_valid_assignment(target_sections, new_slot, assignment.duration,
                                          assignment.instructor_id, assignment.room_id)) {
                        // Remove old assignment
                        for (int s = slot; s < slot + assignment.duration; s++) {
                            if (s < SLOTS_MAX && sec_idx < timetable_[s].size()) {
                                timetable_[s][sec_idx] = TimetableSlot();
                                instructor_busy_[s].erase(assignment.instructor_id);
                                room_busy_[s].erase(assignment.room_id);
                            }
                        }

                        // Place new assignment
                        place_assignment(target_sections, assignment.course_id, assignment.component_id,
                                      assignment.type, assignment.duration, assignment.instructor_id,
                                      assignment.room_id, new_slot);

                        improvements++;
                        break;
                    }
                }
            }
        }

        std::cout << "  Made " << improvements << " improvements" << std::endl;
    }

    // ==================== HELPER METHODS ====================
    std::vector<std::string> get_qualified_instructors(const std::string& qualification,
                                                      const std::string& component_type) const {
        std::vector<std::string> qualified;
        for (const auto& instructor : instructors_) {
            if (is_qualified(instructor, qualification)) {
                if (component_type == "lecture") {
                    if (instructor.type == "professor") {
                        qualified.push_back(instructor.instructor_id);
                    }
                } else {  // lab or tutorial
                    if (instructor.type == "ta" || instructor.type == "part_time") {
                        qualified.push_back(instructor.instructor_id);
                    }
                }
            }
        }
        return qualified;
    }

    bool is_qualified(const Instructor& instructor, const std::string& qualification) const {
        return instructor.qualifications.find(qualification) != instructor.qualifications.end();
    }

    std::vector<std::string> get_suitable_rooms(const std::string& room_type,
                                               const std::string& lab_type,
                                               int min_capacity) const {
        std::vector<std::string> suitable;
        for (const auto& room : rooms_) {
            if (room.type == room_type && room.capacity >= min_capacity) {
                if (room_type == "lab" && !lab_type.empty()) {
                    if (room.lab_type == lab_type) {
                        suitable.push_back(room.room_id);
                    }
                } else {
                    suitable.push_back(room.room_id);
                }
            }
        }

        // Sort by capacity (smallest suitable room first for better utilization)
        std::sort(suitable.begin(), suitable.end(), [&](const std::string& a, const std::string& b) {
            return room_map_.at(a).capacity < room_map_.at(b).capacity;
        });

        return suitable;
    }

    bool is_valid_assignment(const std::vector<int>& target_sections, int slot, int duration,
                            const std::string& instructor_id, const std::string& room_id) const {
        // Basic bounds checking
        if (slot < 0 || slot >= SLOTS_MAX) return false;
        if (slot + duration > SLOTS_MAX) return false;
        if (duration > 1 && slot % 2 != 0) return false;  // 90-min classes must start at period boundary

        // Check instructor availability
        for (int s = slot; s < slot + duration; s++) {
            if (instructor_busy_[s].find(instructor_id) != instructor_busy_[s].end()) {
                return false;
            }
        }

        // Check room availability
        for (int s = slot; s < slot + duration; s++) {
            if (room_busy_[s].find(room_id) != room_busy_[s].end()) {
                return false;
            }
        }

        // Check student conflicts
        for (int sec_idx : target_sections) {
            for (int s = slot; s < slot + duration; s++) {
                if (timetable_[s][sec_idx].is_taken) {
                    return false;
                }
            }
        }

        return true;
    }

    void place_assignment(const std::vector<int>& target_sections, const std::string& course_id,
                         const std::string& component_id, const std::string& type, int duration,
                         const std::string& instructor_id, const std::string& room_id, int slot) {
        for (int sec_idx : target_sections) {
            timetable_[slot][sec_idx].course_id = course_id;
            timetable_[slot][sec_idx].component_id = component_id;
            timetable_[slot][sec_idx].type = type;
            timetable_[slot][sec_idx].room_id = room_id;
            timetable_[slot][sec_idx].instructor_id = instructor_id;
            timetable_[slot][sec_idx].duration = duration;
            timetable_[slot][sec_idx].is_taken = true;
            timetable_[slot][sec_idx].is_continuation = false;
            timetable_[slot][sec_idx].student_count = sections_[sec_idx].student_count;

            // Mark continuation slots
            for (int i = 1; i < duration; i++) {
                timetable_[slot + i][sec_idx].course_id = course_id;
                timetable_[slot + i][sec_idx].component_id = component_id;
                timetable_[slot + i][sec_idx].type = type;
                timetable_[slot + i][sec_idx].room_id = room_id;
                timetable_[slot + i][sec_idx].instructor_id = instructor_id;
                timetable_[slot + i][sec_idx].duration = duration;
                timetable_[slot + i][sec_idx].is_taken = true;
                timetable_[slot + i][sec_idx].is_continuation = true;
                timetable_[slot + i][sec_idx].student_count = sections_[sec_idx].student_count;
            }

            scheduled_components_[sec_idx].insert(component_id);
        }

        // Update availability tracking
        for (int s = slot; s < slot + duration; s++) {
            instructor_busy_[s].insert(instructor_id);
            room_busy_[s].insert(room_id);
        }

        // Update instructor hours
        if (instructor_map_.find(instructor_id) != instructor_map_.end()) {
            instructor_map_[instructor_id].scheduled_hours += duration;
        }

        // Mark component as scheduled
        for (auto& course : courses_) {
            for (auto& component : course.components) {
                if (component.component_id == component_id) {
                    component.is_scheduled = true;
                    break;
                }
            }
        }
    }
};

// ==================== JSON HANDLER ====================
class JsonHandler {
public:
    static bool parse_input(const json& input_data,
                          std::vector<Course>& courses,
                          std::vector<Instructor>& instructors,
                          std::vector<Room>& rooms,
                          std::vector<StudentGroup>& groups,
                          std::vector<Section>& sections,
                          std::vector<std::string>& errors) {
        try {
            errors.clear();

            // Parse courses
            if (input_data.contains("courses")) {
                for (const auto& course_json : input_data["courses"]) {
                    Course course;
                    course.course_id = course_json.value("courseID", "");
                    course.course_name = course_json.value("courseName", "");
                    course.course_type = course_json.value("courseType", "core");
                    course.all_year = course_json.value("allYear", false);

                    if (course_json.contains("components") && course_json["components"].is_array()) {
                        for (const auto& comp_json : course_json["components"]) {
                            CourseComponent component;
                            component.component_id = comp_json.value("componentID", "");
                            component.type = comp_json.value("type", "");
                            component.lab_type = comp_json.value("labType", "");
                            component.duration_slots = comp_json.value("durationSlots", 1);
                            component.min_capacity = comp_json.value("minCapacity", 0);
                            component.instructor_qualification = comp_json.value("instructorQualification", "");
                            component.requires_lecture_first = comp_json.value("requiresLectureFirst", false);
                            component.concurrent_sections = comp_json.value("concurrentSections", false);

                            if (comp_json.contains("studentGroups") && comp_json["studentGroups"].is_array()) {
                                component.student_groups = comp_json["studentGroups"].get<std::vector<std::string>>();
                            }
                            if (comp_json.contains("studentSections") && comp_json["studentSections"].is_array()) {
                                component.student_sections = comp_json["studentSections"].get<std::vector<std::string>>();
                            }

                            course.components.push_back(component);
                        }
                    }

                    if (course.course_id.empty()) {
                        errors.push_back("Course missing courseID");
                        continue;
                    }

                    courses.push_back(course);
                }
            }

            // Parse instructors
            if (input_data.contains("instructors")) {
                for (const auto& inst_json : input_data["instructors"]) {
                    Instructor instructor;
                    instructor.instructor_id = inst_json.value("instructorID", "");
                    instructor.name = inst_json.value("name", "");
                    instructor.type = inst_json.value("type", "professor");
                    instructor.max_hours_weekly = inst_json.value("maxHoursWeekly", 20);

                    if (inst_json.contains("qualifications") && inst_json["qualifications"].is_array()) {
                        auto quals = inst_json["qualifications"].get<std::vector<std::string>>();
                        instructor.qualifications.insert(quals.begin(), quals.end());
                    }
                    if (inst_json.contains("unavailableSlots") && inst_json["unavailableSlots"].is_array()) {
                        auto slots = inst_json["unavailableSlots"].get<std::vector<int>>();
                        instructor.unavailable_slots.insert(slots.begin(), slots.end());
                    }
                    if (inst_json.contains("preferredSlots") && inst_json["preferredSlots"].is_array()) {
                        auto slots = inst_json["preferredSlots"].get<std::vector<int>>();
                        instructor.preferred_slots.insert(slots.begin(), slots.end());
                    }

                    instructors.push_back(instructor);
                }
            }

            // Parse rooms
            if (input_data.contains("rooms")) {
                for (const auto& room_json : input_data["rooms"]) {
                    Room room;
                    room.room_id = room_json.value("roomID", "");
                    room.name = room_json.value("name", "");
                    room.type = room_json.value("type", "");
                    room.lab_type = room_json.value("labType", "");
                    room.capacity = room_json.value("capacity", 0);

                    if (room_json.contains("equipment") && room_json["equipment"].is_array()) {
                        room.equipment = room_json["equipment"].get<std::vector<std::string>>();
                    }

                    rooms.push_back(room);
                }
            }

            // Parse student groups
            if (input_data.contains("studentGroups")) {
                for (const auto& group_json : input_data["studentGroups"]) {
                    StudentGroup group;
                    group.group_id = group_json.value("groupID", "");
                    group.year = group_json.value("year", 1);
                    group.major = group_json.value("major", "general");
                    group.size = group_json.value("size", 0);

                    if (group_json.contains("sections") && group_json["sections"].is_array()) {
                        group.sections = group_json["sections"].get<std::vector<std::string>>();
                    }

                    groups.push_back(group);
                }
            }

            // Parse sections
            if (input_data.contains("sections")) {
                for (const auto& section_json : input_data["sections"]) {
                    Section section;
                    section.section_id = section_json.value("sectionID", "");
                    section.group_id = section_json.value("groupID", "");
                    section.year = section_json.value("year", 1);
                    section.student_count = section_json.value("studentCount", 0);

                    if (section_json.contains("assignedCourses") && section_json["assignedCourses"].is_array()) {
                        section.assigned_courses = section_json["assignedCourses"].get<std::vector<std::string>>();
                    }

                    sections.push_back(section);
                }
            }

            return errors.empty();

        } catch (const std::exception& e) {
            errors.push_back(std::string("JSON parsing error: ") + e.what());
            return false;
        }
    }

    static json create_response(const TimetableSolver& solver) {
        const auto& result = solver.get_result();
        const auto& timetable = solver.get_timetable();
        const auto& sections = solver.get_sections();
        const auto& courses = solver.get_courses();

        json response;
        response["success"] = result.success;
        response["message"] = result.message;

        // Add warnings and errors if any
        if (!result.warnings.empty()) {
            response["warnings"] = result.warnings;
        }
        if (!result.errors.empty()) {
            response["errors"] = result.errors;
        }

        if (result.success) {
            // Build sections schedule
            json sections_json = json::array();

            for (size_t j = 0; j < sections.size(); j++) {
                json section_data;
                section_data["sectionID"] = sections[j].section_id;
                section_data["groupID"] = sections[j].group_id;
                section_data["year"] = sections[j].year;
                section_data["studentCount"] = sections[j].student_count;

                json schedule_json = json::array();
                for (int i = 0; i < TimetableSolver::SLOTS_MAX; i++) {
                    if (i < timetable.size() && j < timetable[i].size() &&
                        timetable[i][j].is_taken && !timetable[i][j].is_continuation) {
                        json slot_json;
                        slot_json["slotIndex"] = i;
                        slot_json["courseID"] = timetable[i][j].course_id;
                        slot_json["componentID"] = timetable[i][j].component_id;
                        slot_json["type"] = timetable[i][j].type;
                        slot_json["roomID"] = timetable[i][j].room_id;
                        slot_json["instructorID"] = timetable[i][j].instructor_id;
                        slot_json["duration"] = timetable[i][j].duration;
                        slot_json["studentCount"] = timetable[i][j].student_count;

                        // Add time information
                        int day = i / 8;
                        int period = i % 8;
                        std::vector<std::string> days = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"};
                        std::vector<std::string> start_times = {"09:00", "09:45", "10:45", "11:30", "12:30", "13:15", "14:15", "15:00"};
                        std::vector<std::string> end_times = {"09:45", "10:30", "11:30", "12:15", "13:15", "14:00", "15:00", "15:45"};

                        slot_json["day"] = days[day];
                        slot_json["period"] = period + 1;
                        slot_json["startTime"] = start_times[period];
                        slot_json["endTime"] = end_times[period + timetable[i][j].duration - 1];

                        schedule_json.push_back(slot_json);
                    }
                }

                section_data["schedule"] = schedule_json;
                sections_json.push_back(section_data);
            }

            response["sections"] = sections_json;

            // Add statistics
            int total_components = 0;
            int scheduled_components = 0;
            for (const auto& course : courses) {
                for (const auto& component : course.components) {
                    total_components++;
                    if (component.is_scheduled) {
                        scheduled_components++;
                    }
                }
            }

            response["statistics"] = {
                {"totalComponents", total_components},
                {"scheduledComponents", scheduled_components},
                {"completionRate", std::to_string(scheduled_components) + "/" + std::to_string(total_components)}
            };
        }

        return response;
    }
};

// ==================== MAIN SERVER ====================
int main() {
    httplib::Server svr;

    // Global error handler to prevent server crashes
    svr.set_exception_handler([](const httplib::Request& req, httplib::Response& res, std::exception_ptr ep) {
        try {
            std::rethrow_exception(ep);
        } catch (const std::exception& e) {
            json error_response;
            error_response["success"] = false;
            error_response["error"] = std::string("Server error: ") + e.what();
            res.set_content(error_response.dump(2), "application/json");
            res.status = 500;
        } catch (...) {
            json error_response;
            error_response["success"] = false;
            error_response["error"] = "Unknown server error";
            res.set_content(error_response.dump(2), "application/json");
            res.status = 500;
        }
    });

    // Schedule endpoint
    svr.Post("/api/schedule", [](const httplib::Request& req, httplib::Response& res) {
        try {
            if (req.body.empty()) {
                json error_response;
                error_response["success"] = false;
                error_response["error"] = "Empty request body";
                res.set_content(error_response.dump(2), "application/json");
                res.status = 400;
                return;
            }

            auto json_data = json::parse(req.body);

            std::vector<Course> courses;
            std::vector<Instructor> instructors;
            std::vector<Room> rooms;
            std::vector<StudentGroup> groups;
            std::vector<Section> sections;
            std::vector<std::string> parse_errors;

            if (!JsonHandler::parse_input(json_data, courses, instructors, rooms, groups, sections, parse_errors)) {
                json error_response;
                error_response["success"] = false;
                error_response["error"] = "Invalid input data";
                if (!parse_errors.empty()) {
                    error_response["parseErrors"] = parse_errors;
                }
                res.set_content(error_response.dump(2), "application/json");
                res.status = 400;
                return;
            }

            TimetableSolver solver;
            auto result = solver.generate_timetable(courses, instructors, rooms, groups, sections);

            json response = JsonHandler::create_response(solver);
            res.set_content(response.dump(2), "application/json");
            res.status = result.success ? 200 : 400;

        } catch (const json::parse_error& e) {
            json error_response;
            error_response["success"] = false;
            error_response["error"] = std::string("JSON parse error: ") + e.what();
            res.set_content(error_response.dump(2), "application/json");
            res.status = 400;
        } catch (const std::exception& e) {
            json error_response;
            error_response["success"] = false;
            error_response["error"] = std::string("Processing error: ") + e.what();
            res.set_content(error_response.dump(2), "application/json");
            res.status = 500;
        }

        res.set_header("Access-Control-Allow-Origin", "*");
    });

    // CORS preflight
    svr.Options("/api/schedule", [](const httplib::Request& req, httplib::Response& res) {
        res.set_header("Access-Control-Allow-Origin", "*");
        res.set_header("Access-Control-Allow-Methods", "POST, OPTIONS");
        res.set_header("Access-Control-Allow-Headers", "Content-Type");
        res.status = 204;
    });

    // Health check
    svr.Get("/health", [](const httplib::Request& req, httplib::Response& res) {
        json response;
        response["status"] = "healthy";
        response["service"] = "timetable_scheduler";
        response["timestamp"] = std::to_string(std::time(nullptr));
        res.set_content(response.dump(2), "application/json");
    });

    // 404 handler
    svr.set_error_handler([](const httplib::Request& req, httplib::Response& res) {
        json response;
        response["success"] = false;
        response["error"] = "Endpoint not found: " + req.path;
        res.set_content(response.dump(2), "application/json");
        res.status = 404;
    });

    std::cout << "==========================================" << std::endl;
    std::cout << "  University Timetable Generator Server  " << std::endl;
    std::cout << "==========================================" << std::endl;
    std::cout << "Server running on: http://localhost:8080" << std::endl;
    std::cout << "Endpoints:" << std::endl;
    std::cout << "  POST /api/schedule - Generate timetable" << std::endl;
    std::cout << "  GET  /health       - Health check" << std::endl;
    std::cout << "==========================================" << std::endl;

    try {
        svr.listen("0.0.0.0", 8080);
    } catch (const std::exception& e) {
        std::cerr << "Server failed to start: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}