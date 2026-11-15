-- join then group
SELECT d.dname, COUNT(*) AS n FROM Emp e JOIN Dept d ON e.did = d.did WHERE e.salary > 100000 GROUP BY d.dname;