-- Required case 4: same inputs, different plans
SELECT city FROM Emp UNION SELECT city FROM Emp;
SELECT city FROM Emp UNION ALL SELECT city FROM Emp;