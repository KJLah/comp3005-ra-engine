-- the same query as q07 written with commas
SELECT e.name, d.dname FROM Emp e, Dept d WHERE e.did = d.did AND e.age > 60 AND d.budget > 100000;