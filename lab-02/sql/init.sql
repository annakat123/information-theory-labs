CREATE TABLE accounts (
    account_id bigint PRIMARY KEY,
    owner_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('active', 'blocked', 'closed'))
);

ALTER TABLE accounts REPLICA IDENTITY FULL;

INSERT INTO accounts VALUES
    (1001, 'Иванов Илья', 'active'),
    (1002, 'Петрова Анна', 'active'),
    (1003, 'Смирнов Олег', 'active');
