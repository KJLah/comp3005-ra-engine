-- foreign-key join written with JOIN ON
SELECT e.name, d.dname FROM Emp e JOIN Dept d ON e.did = d.did WHERE e.age > 60 AND d.budget > 100000;