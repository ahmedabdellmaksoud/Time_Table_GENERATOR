# Back-End for Timetable Generator
this algorithm is implemented in c++ and is responsible for generating timetables based on user inputs and constraints.
right now i am to lazy to write the documentation for this folder.
so you can find the compiled version of it in the same directory.
run it and use this command to see the sample output 
```bash
# Make executable (if needed) and run the generator:
./timetable_generator

curl -X POST http://localhost:8080/api/schedule \
  -H "Content-Type: application/json" \
  -d @test_data.json
  
```

the front end is still under development so you have to use postman or curl to test the api.