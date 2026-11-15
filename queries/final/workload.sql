-- Rehearsal workload: the 20 Module 4 queries, then 5 that cover the rest of the subset.
-- q01 (from q01_eq.sql)
SELECT name, salary FROM Emp WHERE city = 'Toronto';
-- q02 (from q02_range.sql)
SELECT eid, salary FROM Emp WHERE age > 60;
-- q03 (from q03_and.sql)
SELECT name FROM Emp WHERE did = 7 AND age > 60;
-- q04 (from q04_varchar_range.sql)
SELECT name, city FROM Emp WHERE city < 'L';
-- q05 (from q05_or.sql)
SELECT eid FROM Emp WHERE age < 25 OR age > 60;
-- q06 (from q06_not.sql)
SELECT eid, city FROM Emp WHERE NOT (age >= 30) AND city <> 'Ottawa';
-- q07 (from q07_join_on.sql)
SELECT e.name, d.dname FROM Emp e JOIN Dept d ON e.did = d.did WHERE e.age > 60 AND d.budget > 100000;
-- q08 (from q08_join_commas.sql)
SELECT e.name, d.dname FROM Emp e, Dept d WHERE e.did = d.did AND e.age > 60 AND d.budget > 100000;
-- q09 (from q09_three_way.sql)
SELECT e.name, p.pname, w.hours FROM Emp e JOIN Works w ON e.eid = w.eid JOIN Proj p ON w.pid = p.pid WHERE p.budget > 90000;
-- q10 (from q10_four_way.sql)
SELECT e.name, d.dname, p.pname FROM Emp e JOIN Works w ON e.eid = w.eid JOIN Proj p ON w.pid = p.pid JOIN Dept d ON e.did = d.did WHERE d.budget < 50000 AND w.hours > 30;
-- q11 (from q11_group_having.sql)
SELECT did, COUNT(*) AS n, AVG(salary) AS pay FROM Emp GROUP BY did HAVING COUNT(*) > 55;
-- q12 (from q12_group_key_filter.sql)
SELECT did, COUNT(*) AS n FROM Emp GROUP BY did HAVING did < 10;
-- q13 (from q13_distinct.sql)
SELECT DISTINCT city, province FROM Emp;
-- q14 (from q14_order_limit.sql)
SELECT name, salary FROM Emp WHERE age < 30 ORDER BY salary DESC LIMIT 10;
-- q15 (from q15_union_pushdown.sql)
SELECT u.city FROM (SELECT city FROM Emp UNION ALL SELECT city FROM Dept) AS u WHERE u.city = 'Ottawa';
-- q16 (from q16_self_join.sql)
SELECT a.dname, b.dname FROM Dept a JOIN Dept b ON a.city = b.city WHERE a.did < b.did;
-- q17 (from q17_correlated_join.sql)
SELECT e.name, e.note, w.hours FROM Emp e JOIN Works w ON e.eid = w.eid WHERE e.city = 'Ottawa' AND e.area = '201';
-- q18 (from q18_correlated_count.sql)
SELECT COUNT(*) AS n FROM Emp WHERE city = 'Ottawa' AND province = 'ON';
-- q19 (from q19_join_group.sql)
SELECT d.dname, COUNT(*) AS n FROM Emp e JOIN Dept d ON e.did = d.did WHERE e.salary > 100000 GROUP BY d.dname;
-- q20 (from q20_works_proj.sql)
SELECT w.eid, p.pname FROM Works w JOIN Proj p ON w.pid = p.pid WHERE p.did = 3 AND w.role = 'lead';
-- q21 intersect
SELECT city FROM Emp INTERSECT SELECT city FROM Dept;
-- q22 except: employees who work on no project
SELECT eid FROM Emp EXCEPT SELECT eid FROM Works;
-- q23 aggregate in a derived table, filtered outside
SELECT t.did, t.n FROM (SELECT did, COUNT(*) AS n FROM Proj GROUP BY did) AS t WHERE t.n > 10;
-- q24 three-way join, then SUM and MAX per group, ordered
SELECT p.pname, SUM(w.hours) AS h, MAX(e.salary) AS top FROM Works w JOIN Proj p ON w.pid = p.pid JOIN Emp e ON w.eid = e.eid WHERE p.budget > 95000 GROUP BY p.pname ORDER BY h DESC;
-- q25 star, ordered
SELECT * FROM Dept WHERE budget >= 150000 ORDER BY did;