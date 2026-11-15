-- correlated predicates alone (city decides province)
SELECT COUNT(*) AS n FROM Emp WHERE city = 'Ottawa' AND province = 'ON';