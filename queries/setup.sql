-- Test catalog for the Module 3 examples:  python3 -m sql exec --db m3db --file queries/setup.sql
CREATE TABLE Emp (eid INT, name VARCHAR(20), age INT, salary INT, did INT, mgr INT,
                  city VARCHAR(20));
CREATE TABLE Dept (did INT, title VARCHAR(20), budget INT, city VARCHAR(20));
CREATE TABLE Proj (pid INT, pname VARCHAR(20), did INT);
CREATE TABLE Works (eid INT, pid INT, hours INT);

INSERT INTO Emp VALUES (1, 'Ann', 40, 100, 1, 0, 'Ottawa');
INSERT INTO Emp VALUES (2, 'Bob', 25, 80, 1, 1, 'Ottawa');
INSERT INTO Emp VALUES (3, 'Cy', 35, 90, 2, 1, 'Toronto');
INSERT INTO Emp VALUES (4, 'Di', 35, 90, 2, 1, 'Toronto');
INSERT INTO Emp VALUES (5, 'Ed', 50, 120, 3, 0, 'Ottawa');
INSERT INTO Dept VALUES (1, 'Eng', 500, 'Ottawa');
INSERT INTO Dept VALUES (2, 'Ops', 50, 'Toronto');
INSERT INTO Proj VALUES (10, 'Engine', 1);
INSERT INTO Proj VALUES (20, 'Payroll', 2);
INSERT INTO Works VALUES (1, 10, 5);
INSERT INTO Works VALUES (3, 20, 8);