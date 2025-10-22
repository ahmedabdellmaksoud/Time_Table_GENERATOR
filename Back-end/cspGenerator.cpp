#include "httplib.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <vector>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <algorithm>
#include <iomanip>
#include <sstream>
#include <queue>
#include <set>
#include <cmath>

using json = nlohmann::json;
using namespace httplib;
using namespace std;

#define all(x) (x).begin(),(x).end()

// Simplified Course Structure - NO FREQUENCY
struct CourseComponent {
    string componentID;
    string type; // "lecture", "lab", "tutorial"
    string labType;
    int durationSlots;  // 1 = 45min, 2 = 90min
    int minCapacity;
    string instructorQualification;
    bool requiresLectureFirst;
    bool concurrentSections;
    vector<string> studentGroups;
    vector<string> studentSections;

    // Tracking - simple boolean instead of count
    bool isScheduled;
};

struct Course {
    string courseID;
    string courseName;
    string courseType;
    vector<CourseComponent> components;
    bool allYear;
};

struct Instructor {
    string instructorID;
    string name;
    string type; // "professor", "ta", "part_time"
    set<string> qualifications;
    int maxHoursWeekly;
    set<int> unavailableSlots;
    set<int> preferredSlots;
    int scheduledHours;
};

struct Room {
    string roomID;
    string name;
    string type; // "lecture", "lab", "classroom"
    string labType;
    int capacity;
    vector<string> equipment;
};

struct StudentGroup {
    string groupID;
    int year;
    string major;
    vector<string> sections;
    int size;
};

struct Section {
    string sectionID;
    string groupID;
    int year;
    int studentCount;
    vector<string> assignedCourses;
};

struct Slot {
    string courseID;
    string componentID;
    string type;
    string roomID;
    string instructorID;
    int duration;
    bool istaken;
    bool isCont;
    int studentCount;

    Slot() : duration(0), istaken(false), isCont(false), studentCount(0) {}
};

// Global Data Structures
vector<Course> courses;
vector<Instructor> instructors;
vector<Room> rooms;
vector<Section> sections;
vector<StudentGroup> groups;

const int SLOTS_MAX = 40;
int SECTIONS_MAX;
vector<vector<Slot>> Timetable;

// Enhanced Tracking Structures
unordered_map<string, int> sectionToIndex;
unordered_map<string, string> sectionToGroup;
unordered_map<string, vector<string>> groupToSections;
unordered_map<int, vector<string>> yearToSections;
unordered_map<string, Course> getCourse;
unordered_map<string, Instructor> getInstructor;
unordered_map<string, Room> getRoom;

// Availability Tracking
unordered_set<string> instructorBusy[SLOTS_MAX];
unordered_set<string> roomBusy[SLOTS_MAX];
unordered_set<string> studentGroupBusy[SLOTS_MAX];
vector<unordered_set<string>> scheduledComponents;

// Helper Functions
int getSlotCost(int slot) {
    if (slot < 4 || slot > 35) return 10;  // Early morning or late evening
    if (slot < 10 || slot > 29) return 3;   // Less preferred
    return 1;                               // Preferred middle slots
}

bool isQualified(const Instructor& instructor, const string& qualification) {
    return instructor.qualifications.find(qualification) != instructor.qualifications.end();
}

vector<string> getQualifiedInstructors(const string& qualification, const string& componentType) {
    vector<string> qualified;
    for (const auto& instructor : instructors) {
        if (isQualified(instructor, qualification)) {
            if ((componentType == "lecture" && instructor.type == "professor") ||
                (componentType != "lecture" && instructor.type != "professor")) {
                qualified.push_back(instructor.instructorID);
            }
        }
    }
    return qualified;
}

vector<string> getSuitableRooms(const string& roomType, const string& labType, int minCapacity) {
    vector<string> suitable;
    for (const auto& room : rooms) {
        if (room.type == roomType && room.capacity >= minCapacity) {
            if (roomType == "lab" && !labType.empty()) {
                if (room.labType == labType) {
                    suitable.push_back(room.roomID);
                }
            } else {
                suitable.push_back(room.roomID);
            }
        }
    }

    // Sort by capacity (smallest suitable room first)
    sort(suitable.begin(), suitable.end(), [&](const string& a, const string& b) {
        return getRoom[a].capacity < getRoom[b].capacity;
    });

    return suitable;
}

bool isValidAssignment(const vector<int>& targetSections, int slot, int duration,
                      const string& instructorID, const string& roomID) {
    if (duration > 1 && slot % 2 != 0) return false; // 90-min classes must start at period boundary
    if (slot < 0 || slot >= SLOTS_MAX) return false;
    if (slot + duration > SLOTS_MAX) return false;

    // Check instructor availability
    for (int s = slot; s < slot + duration; s++) {
        if (instructorBusy[s].find(instructorID) != instructorBusy[s].end()) {
            return false;
        }
    }

    // Check room availability
    for (int s = slot; s < slot + duration; s++) {
        if (roomBusy[s].find(roomID) != roomBusy[s].end()) {
            return false;
        }
    }

    // Check student group conflicts
    for (int secIdx : targetSections) {
        const Section& section = sections[secIdx];
        for (int s = slot; s < slot + duration; s++) {
            if (Timetable[s][secIdx].istaken) {
                return false;
            }
        }
    }

    return true;
}

void placeAssignment(const vector<int>& targetSections, const string& courseID,
                    const string& componentID, const string& type, int duration,
                    const string& instructorID, const string& roomID, int slot) {

    for (int secIdx : targetSections) {
        Timetable[slot][secIdx].courseID = courseID;
        Timetable[slot][secIdx].componentID = componentID;
        Timetable[slot][secIdx].type = type;
        Timetable[slot][secIdx].roomID = roomID;
        Timetable[slot][secIdx].instructorID = instructorID;
        Timetable[slot][secIdx].duration = duration;
        Timetable[slot][secIdx].istaken = true;
        Timetable[slot][secIdx].isCont = false;
        Timetable[slot][secIdx].studentCount = sections[secIdx].studentCount;

        for (int i = 1; i < duration; i++) {
            Timetable[slot + i][secIdx].courseID = courseID;
            Timetable[slot + i][secIdx].componentID = componentID;
            Timetable[slot + i][secIdx].type = type;
            Timetable[slot + i][secIdx].roomID = roomID;
            Timetable[slot + i][secIdx].instructorID = instructorID;
            Timetable[slot + i][secIdx].duration = duration;
            Timetable[slot + i][secIdx].istaken = true;
            Timetable[slot + i][secIdx].isCont = true;
            Timetable[slot + i][secIdx].studentCount = sections[secIdx].studentCount;
        }

        scheduledComponents[secIdx].insert(componentID);
    }

    // Update availability
    for (int s = slot; s < slot + duration; s++) {
        instructorBusy[s].insert(instructorID);
        roomBusy[s].insert(roomID);
    }

    // Update instructor hours
    getInstructor[instructorID].scheduledHours += duration;

    // Mark component as scheduled
    for (auto& course : courses) {
        for (auto& component : course.components) {
            if (component.componentID == componentID) {
                component.isScheduled = true;
                break;
            }
        }
    }
}

// Multi-phase scheduling - SIMPLIFIED (NO FREQUENCY LOOPS)
bool scheduleLectures() {
    cout << "Phase 1: Scheduling Lectures..." << endl;

    vector<pair<Course*, CourseComponent*>> lectures;

    // Collect all lectures
    for (auto& course : courses) {
        for (auto& component : course.components) {
            if (component.type == "lecture" && !component.isScheduled) {
                lectures.push_back({&course, &component});
            }
        }
    }

    // Sort by difficulty: largest capacity requirements first
    sort(lectures.begin(), lectures.end(),
         [](const pair<Course*, CourseComponent*>& a, const pair<Course*, CourseComponent*>& b) {
             return a.second->minCapacity > b.second->minCapacity;
         });

    for (auto& lecture : lectures) {
        Course* course = lecture.first;
        CourseComponent* component = lecture.second;

        if (component->isScheduled) continue; // Skip if already scheduled

        bool scheduled = false;

        // Get target sections for this lecture
        vector<int> targetSections;
        for (const auto& groupID : component->studentGroups) {
            if (groupToSections.find(groupID) != groupToSections.end()) {
                for (const auto& sectionID : groupToSections[groupID]) {
                    int secIdx = sectionToIndex[sectionID];
                    // Check if this section needs this course and hasn't been scheduled for this component
                    if (find(sections[secIdx].assignedCourses.begin(),
                            sections[secIdx].assignedCourses.end(), course->courseID) != sections[secIdx].assignedCourses.end() &&
                        scheduledComponents[secIdx].find(component->componentID) == scheduledComponents[secIdx].end()) {
                        targetSections.push_back(secIdx);
                    }
                }
            }
        }

        if (targetSections.empty()) {
            cout << "  ⚠ No target sections for " << course->courseID << " lecture" << endl;
            continue;
        }

        // Get qualified instructors and suitable rooms
        auto qualifiedInstructors = getQualifiedInstructors(component->instructorQualification, "lecture");
        auto suitableRooms = getSuitableRooms("lecture", "", component->minCapacity);

        if (qualifiedInstructors.empty() || suitableRooms.empty()) {
            cout << "  ⚠ No resources for " << course->courseID << " lecture" << endl;
            continue;
        }

        // Try preferred slots first (middle of day)
        vector<int> slotOrder;
        for (int i = 10; i < 30; i++) {
            slotOrder.push_back(i);
        }
        // Add remaining slots
        for (int i = 0; i < SLOTS_MAX; i++) {
            if (find(slotOrder.begin(), slotOrder.end(), i) == slotOrder.end()) {
                slotOrder.push_back(i);
            }
        }

        for (int slot : slotOrder) {
            for (const auto& instructorID : qualifiedInstructors) {
                for (const auto& roomID : suitableRooms) {
                    if (isValidAssignment(targetSections, slot, component->durationSlots, instructorID, roomID)) {
                        placeAssignment(targetSections, course->courseID, component->componentID,
                                      "lecture", component->durationSlots, instructorID, roomID, slot);
                        scheduled = true;
                        cout << "  ✓ Scheduled " << course->courseID << " lecture at slot " << slot << endl;
                        break;
                    }
                }
                if (scheduled) break;
            }
            if (scheduled) break;
        }

        if (!scheduled) {
            cout << "  ✗ Failed to schedule " << course->courseID << " lecture" << endl;
        }
    }

    return true;
}

bool scheduleLabs() {
    cout << "Phase 2: Scheduling Labs..." << endl;

    vector<pair<Course*, CourseComponent*>> labs;

    // Collect all unscheduled labs, prioritize specialized labs first
    for (auto& course : courses) {
        for (auto& component : course.components) {
            if (component.type == "lab" && !component.isScheduled) {
                labs.push_back({&course, &component});
            }
        }
    }

    // Sort by lab type specialization
    sort(labs.begin(), labs.end(),
         [](const pair<Course*, CourseComponent*>& a, const pair<Course*, CourseComponent*>& b) {
             string labTypeA = a.second->labType;
             string labTypeB = b.second->labType;

             if (labTypeA == "electronics_lab" && labTypeB != "electronics_lab") return true;
             if (labTypeA == "physics_lab" && labTypeB != "physics_lab" && labTypeB != "electronics_lab") return true;
             return false;
         });

    for (auto& lab : labs) {
        Course* course = lab.first;
        CourseComponent* component = lab.second;

        if (component->isScheduled) continue;

        // For labs, schedule each section individually
        for (const auto& sectionID : component->studentSections) {
            if (sectionToIndex.find(sectionID) == sectionToIndex.end()) continue;

            int secIdx = sectionToIndex[sectionID];

            // Check if already scheduled
            if (scheduledComponents[secIdx].find(component->componentID) != scheduledComponents[secIdx].end()) {
                continue;
            }

            vector<int> targetSections = {secIdx};
            bool scheduled = false;

            auto qualifiedInstructors = getQualifiedInstructors(component->instructorQualification, "lab");
            auto suitableRooms = getSuitableRooms("lab", component->labType, component->minCapacity);

            if (qualifiedInstructors.empty() || suitableRooms.empty()) {
                cout << "  ⚠ No resources for " << course->courseID << " lab section " << sectionID << endl;
                continue;
            }

            // Try preferred middle slots first
            vector<int> slotOrder;
            for (int i = 10; i < 30; i++) {
                slotOrder.push_back(i);
            }
            for (int i = 0; i < SLOTS_MAX; i++) {
                if (find(slotOrder.begin(), slotOrder.end(), i) == slotOrder.end()) {
                    slotOrder.push_back(i);
                }
            }

            for (int slot : slotOrder) {
                for (const auto& instructorID : qualifiedInstructors) {
                    for (const auto& roomID : suitableRooms) {
                        if (isValidAssignment(targetSections, slot, component->durationSlots, instructorID, roomID)) {
                            placeAssignment(targetSections, course->courseID, component->componentID,
                                          "lab", component->durationSlots, instructorID, roomID, slot);
                            scheduled = true;
                            cout << "  ✓ Scheduled " << course->courseID << " lab for section " << sectionID << " at slot " << slot << endl;
                            break;
                        }
                    }
                    if (scheduled) break;
                }
                if (scheduled) break;
            }

            if (!scheduled) {
                cout << "  ✗ Failed to schedule " << course->courseID << " lab for section " << sectionID << endl;
            }
        }
    }

    return true;
}

bool scheduleTutorials() {
    cout << "Phase 3: Scheduling Tutorials..." << endl;

    vector<pair<Course*, CourseComponent*>> tutorials;

    for (auto& course : courses) {
        for (auto& component : course.components) {
            if (component.type == "tutorial" && !component.isScheduled) {
                tutorials.push_back({&course, &component});
            }
        }
    }

    for (auto& tutorial : tutorials) {
        Course* course = tutorial.first;
        CourseComponent* component = tutorial.second;

        if (component->isScheduled) continue;

        for (const auto& sectionID : component->studentSections) {
            if (sectionToIndex.find(sectionID) == sectionToIndex.end()) continue;

            int secIdx = sectionToIndex[sectionID];

            if (scheduledComponents[secIdx].find(component->componentID) != scheduledComponents[secIdx].end()) {
                continue;
            }

            vector<int> targetSections = {secIdx};
            bool scheduled = false;

            auto qualifiedInstructors = getQualifiedInstructors(component->instructorQualification, "tutorial");
            auto suitableRooms = getSuitableRooms("classroom", "", component->minCapacity);

            if (qualifiedInstructors.empty() || suitableRooms.empty()) {
                cout << "  ⚠ No resources for " << course->courseID << " tutorial section " << sectionID << endl;
                continue;
            }

            // Try any available slot
            for (int slot = 0; slot < SLOTS_MAX && !scheduled; slot++) {
                for (const auto& instructorID : qualifiedInstructors) {
                    for (const auto& roomID : suitableRooms) {
                        if (isValidAssignment(targetSections, slot, component->durationSlots, instructorID, roomID)) {
                            placeAssignment(targetSections, course->courseID, component->componentID,
                                          "tutorial", component->durationSlots, instructorID, roomID, slot);
                            scheduled = true;
                            cout << "  ✓ Scheduled " << course->courseID << " tutorial for section " << sectionID << " at slot " << slot << endl;
                            break;
                        }
                    }
                    if (scheduled) break;
                }
            }

            if (!scheduled) {
                cout << "  ✗ Failed to schedule " << course->courseID << " tutorial for section " << sectionID << endl;
            }
        }
    }

    return true;
}

bool optimizeSchedule() {
    cout << "Phase 4: Optimizing Schedule..." << endl;

    set<int> undesirableSlots = {0,1,2,3,4,5,36,37,38,39};
    int improvements = 0;

    for (int slot : undesirableSlots) {
        for (int secIdx = 0; secIdx < SECTIONS_MAX; secIdx++) {
            if (Timetable[slot][secIdx].istaken && !Timetable[slot][secIdx].isCont) {
                Slot& assignment = Timetable[slot][secIdx];

                // Try to find a better slot (10-29 are preferred)
                for (int newSlot = 10; newSlot < 30; newSlot++) {
                    vector<int> targetSections = {secIdx};

                    if (isValidAssignment(targetSections, newSlot, assignment.duration,
                                        assignment.instructorID, assignment.roomID)) {

                        // Remove old assignment
                        for (int s = slot; s < slot + assignment.duration; s++) {
                            Timetable[s][secIdx] = Slot();
                            instructorBusy[s].erase(assignment.instructorID);
                            roomBusy[s].erase(assignment.roomID);
                        }

                        // Place new assignment
                        placeAssignment(targetSections, assignment.courseID, assignment.componentID,
                                      assignment.type, assignment.duration, assignment.instructorID,
                                      assignment.roomID, newSlot);

                        improvements++;
                        cout << "  ↪ Moved " << assignment.courseID << " from slot " << slot << " to " << newSlot << endl;
                        break;
                    }
                }
            }
        }
    }

    cout << "  ✓ Made " << improvements << " improvements" << endl;
    return true;
}

bool generateTimetable() {
    cout << "Starting Simplified Timetable Generation..." << endl;

    // Initialize tracking
    Timetable.resize(SLOTS_MAX, vector<Slot>(SECTIONS_MAX, Slot()));
    scheduledComponents.resize(SECTIONS_MAX, unordered_set<string>());

    // Multi-phase scheduling
    if (!scheduleLectures()) return false;
    if (!scheduleLabs()) return false;
    if (!scheduleTutorials()) return false;

    // Optimization
    optimizeSchedule();

    cout << "✓ Timetable generation completed successfully!" << endl;
    return true;
}

void clearData() {
    courses.clear();
    instructors.clear();
    rooms.clear();
    sections.clear();
    groups.clear();

    getCourse.clear();
    getInstructor.clear();
    getRoom.clear();
    sectionToIndex.clear();
    sectionToGroup.clear();
    groupToSections.clear();
    yearToSections.clear();
    scheduledComponents.clear();
    Timetable.clear();

    for (int i = 0; i < SLOTS_MAX; i++) {
        instructorBusy[i].clear();
        roomBusy[i].clear();
        studentGroupBusy[i].clear();
    }
}

void parseInputData(const json& inputData) {
    clearData();

    // Parse courses with components
    if (inputData.contains("courses")) {
        for (auto c : inputData["courses"]) {
            Course course;
            course.courseID = c.value("courseID", "");
            course.courseName = c.value("courseName", "");
            course.courseType = c.value("courseType", "core");
            course.allYear = c.value("allYear", false);

            if (c.contains("components") && c["components"].is_array()) {
                for (auto comp : c["components"]) {
                    CourseComponent component;
                    component.componentID = comp.value("componentID", "");
                    component.type = comp.value("type", "");
                    component.labType = comp.value("labType", "");
                    component.durationSlots = comp.value("durationSlots", 1);
                    component.minCapacity = comp.value("minCapacity", 0);
                    component.instructorQualification = comp.value("instructorQualification", "");
                    component.requiresLectureFirst = comp.value("requiresLectureFirst", false);
                    component.concurrentSections = comp.value("concurrentSections", false);
                    component.isScheduled = false; // Initialize as not scheduled

                    if (comp.contains("studentGroups") && comp["studentGroups"].is_array()) {
                        component.studentGroups = comp["studentGroups"].get<vector<string>>();
                    }
                    if (comp.contains("studentSections") && comp["studentSections"].is_array()) {
                        component.studentSections = comp["studentSections"].get<vector<string>>();
                    }

                    course.components.push_back(component);
                }
            }

            courses.push_back(course);
            getCourse[course.courseID] = course;
        }
    }

    // Parse instructors
    if (inputData.contains("instructors")) {
        for (auto i : inputData["instructors"]) {
            Instructor instructor;
            instructor.instructorID = i.value("instructorID", "");
            instructor.name = i.value("name", "");
            instructor.type = i.value("type", "professor");
            instructor.maxHoursWeekly = i.value("maxHoursWeekly", 20);
            instructor.scheduledHours = 0;

            if (i.contains("qualifications") && i["qualifications"].is_array()) {
                instructor.qualifications = set<string>(i["qualifications"].begin(), i["qualifications"].end());
            }
            if (i.contains("unavailableSlots") && i["unavailableSlots"].is_array()) {
                instructor.unavailableSlots = set<int>(i["unavailableSlots"].begin(), i["unavailableSlots"].end());
            }
            if (i.contains("preferredSlots") && i["preferredSlots"].is_array()) {
                instructor.preferredSlots = set<int>(i["preferredSlots"].begin(), i["preferredSlots"].end());
            }

            instructors.push_back(instructor);
            getInstructor[instructor.instructorID] = instructor;
        }
    }

    // Parse rooms
    if (inputData.contains("rooms")) {
        for (auto r : inputData["rooms"]) {
            Room room;
            room.roomID = r.value("roomID", "");
            room.name = r.value("name", "");
            room.type = r.value("type", "");
            room.labType = r.value("labType", "");
            room.capacity = r.value("capacity", 0);

            if (r.contains("equipment") && r["equipment"].is_array()) {
                room.equipment = r["equipment"].get<vector<string>>();
            }

            rooms.push_back(room);
            getRoom[room.roomID] = room;
        }
    }

    // Parse student groups
    if (inputData.contains("studentGroups")) {
        for (auto g : inputData["studentGroups"]) {
            StudentGroup group;
            group.groupID = g.value("groupID", "");
            group.year = g.value("year", 1);
            group.major = g.value("major", "general");
            group.size = g.value("size", 0);

            if (g.contains("sections") && g["sections"].is_array()) {
                group.sections = g["sections"].get<vector<string>>();
            }

            groups.push_back(group);

            for (auto sec : group.sections) {
                sectionToGroup[sec] = group.groupID;
                groupToSections[group.groupID].push_back(sec);
            }
        }
    }

    // Parse sections
    if (inputData.contains("sections")) {
        int idx = 0;
        for (auto s : inputData["sections"]) {
            Section section;
            section.sectionID = s.value("sectionID", "");
            section.groupID = s.value("groupID", "");
            section.year = s.value("year", 1);
            section.studentCount = s.value("studentCount", 0);

            if (s.contains("assignedCourses") && s["assignedCourses"].is_array()) {
                section.assignedCourses = s["assignedCourses"].get<vector<string>>();
            }

            sections.push_back(section);
            sectionToIndex[section.sectionID] = idx;
            yearToSections[section.year].push_back(section.sectionID);
            idx++;
        }
    }

    SECTIONS_MAX = sections.size();
}

json timetableToJson() {
    json result;
    result["success"] = true;
    result["message"] = "Timetable generated successfully with simplified algorithm";
    result["slotsMax"] = SLOTS_MAX;
    result["sectionsMax"] = SECTIONS_MAX;

    // Statistics
    int totalAssignments = 0;
    int scheduledComponentsCount = 0;
    int totalComponents = 0;

    for (const auto& course : courses) {
        for (const auto& component : course.components) {
            totalComponents++;
            if (component.isScheduled) {
                scheduledComponentsCount++;
            }
        }
    }

    for (int i = 0; i < SLOTS_MAX; i++) {
        for (int j = 0; j < SECTIONS_MAX; j++) {
            if (Timetable[i][j].istaken && !Timetable[i][j].isCont) {
                totalAssignments++;
            }
        }
    }

    result["statistics"] = {
        {"totalAssignments", totalAssignments},
        {"scheduledComponents", scheduledComponentsCount},
        {"totalComponents", totalComponents},
        {"completionRate", to_string(scheduledComponentsCount) + "/" + to_string(totalComponents)}
    };

    // Sections schedule
    json sectionsSchedule = json::array();
    for (size_t j = 0; j < sections.size(); j++) {
        json sectionData;
        sectionData["sectionID"] = sections[j].sectionID;
        sectionData["groupID"] = sections[j].groupID;
        sectionData["year"] = sections[j].year;
        sectionData["studentCount"] = sections[j].studentCount;

        json schedule = json::array();
        for (int i = 0; i < SLOTS_MAX; i++) {
            if (Timetable[i][j].istaken && !Timetable[i][j].isCont) {
                json slot;
                slot["slotIndex"] = i;
                slot["courseID"] = Timetable[i][j].courseID;
                slot["componentID"] = Timetable[i][j].componentID;
                slot["courseName"] = getCourse[Timetable[i][j].courseID].courseName;
                slot["type"] = Timetable[i][j].type;
                slot["roomID"] = Timetable[i][j].roomID;
                slot["roomName"] = getRoom[Timetable[i][j].roomID].name;
                slot["instructorID"] = Timetable[i][j].instructorID;
                slot["instructorName"] = getInstructor[Timetable[i][j].instructorID].name;
                slot["duration"] = Timetable[i][j].duration;
                slot["studentCount"] = Timetable[i][j].studentCount;

                // Add time information
                int day = i / 8;
                int period = i % 8;
                vector<string> days = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"};
                vector<string> startTimes = {"09:00", "09:45", "10:45", "11:30", "12:30", "13:15", "14:15", "15:00"};
                vector<string> endTimes = {"09:45", "10:30", "11:30", "12:15", "13:15", "14:00", "15:00", "15:45"};

                slot["day"] = days[day];
                slot["period"] = period + 1;
                slot["startTime"] = startTimes[period];
                slot["endTime"] = endTimes[period + Timetable[i][j].duration - 1];

                schedule.push_back(slot);
            }
        }

        sectionData["schedule"] = schedule;
        sectionsSchedule.push_back(sectionData);
    }

    result["sections"] = sectionsSchedule;

    // Add warnings for unscheduled components
    json warnings = json::array();
    for (const auto& course : courses) {
        for (const auto& component : course.components) {
            if (!component.isScheduled) {
                warnings.push_back("Course " + course.courseID + " " + component.type + " not scheduled");
            }
        }
    }

    if (!warnings.empty()) {
        result["warnings"] = warnings;
    }

    return result;
}

int main() {
    Server svr;

    svr.Post("/api/schedule", [](const Request& req, Response& res) {
        try {
            json inputData = json::parse(req.body);
            parseInputData(inputData);

            bool success = generateTimetable();

            json response;
            if (success) {
                response = timetableToJson();
            } else {
                response["success"] = false;
                response["error"] = "Failed to generate timetable";
            }

            res.set_content(response.dump(2), "application/json");
        } catch (const exception& e) {
            json errorResponse;
            errorResponse["success"] = false;
            errorResponse["error"] = string("Exception: ") + e.what();
            res.set_content(errorResponse.dump(2), "application/json");
        }

        res.set_header("Access-Control-Allow-Origin", "*");
        res.status = 200;
    });

    svr.Options("/api/schedule", [](const Request& req, Response& res) {
        res.set_header("Access-Control-Allow-Origin", "*");
        res.set_header("Access-Control-Allow-Methods", "POST, OPTIONS");
        res.set_header("Access-Control-Allow-Headers", "Content-Type");
        res.status = 204;
    });

    cout << "Simplified Timetable Scheduling API Server" << endl;
    cout << "Server running on: http://localhost:8080" << endl;
    cout << "Endpoint: POST /api/schedule" << endl;

    svr.listen("0.0.0.0", 8080);
    return 0;
}