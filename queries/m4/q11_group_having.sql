-- HAVING on an aggregate: may not be pushed (rule 5)
SELECT did, COUNT(*) AS n, AVG(salary) AS pay FROM Emp GROUP BY did HAVING COUNT(*) > 55;