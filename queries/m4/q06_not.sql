-- negation and <>
SELECT eid, city FROM Emp WHERE NOT (age >= 30) AND city <> 'Ottawa';