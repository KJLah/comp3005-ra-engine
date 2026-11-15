-- HAVING on the grouping key: pushed below the group_by (rule 5)
SELECT did, COUNT(*) AS n FROM Emp GROUP BY did HAVING did < 10;