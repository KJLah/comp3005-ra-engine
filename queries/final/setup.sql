-- Build the rehearsal database from the CSV files of gen_final.py.
-- Run from the repository root:  radb --db data/finaldb --file queries/final/setup.sql
CREATE TABLE Dept (did INT, dname VARCHAR(20), budget INT, city VARCHAR(16));
CREATE TABLE Emp (eid INT, name VARCHAR(24), age INT, salary INT, did INT,
                  city VARCHAR(16), province VARCHAR(4), area VARCHAR(4), note VARCHAR(80));
CREATE TABLE Proj (pid INT, pname VARCHAR(20), did INT, budget INT);
CREATE TABLE Works (eid INT, pid INT, hours INT, role VARCHAR(12), note VARCHAR(80));
LOAD Dept FROM 'data/final/Dept.csv' HEADER;
LOAD Emp FROM 'data/final/Emp.csv' HEADER;
LOAD Proj FROM 'data/final/Proj.csv' HEADER;
LOAD Works FROM 'data/final/Works.csv' HEADER;
ANALYZE;