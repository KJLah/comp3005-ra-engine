-- Required case 5: WHERE below the grouping, HAVING above it
SELECT did, COUNT(*) AS n FROM Emp WHERE age > 25 GROUP BY did HAVING COUNT(*) > 1;