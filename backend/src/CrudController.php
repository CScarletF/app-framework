<?php

class CrudController
{
    private PDO $db;
    private array $tables;

    public function __construct(PDO $db, array $tables)
    {
        $this->db = $db;
        $this->tables = $tables;
    }

    private function getTableConfig(string $table): array
    {
        if (!isset($this->tables[$table])) {
            $this->jsonError(404, "Unknown table: {$table}");
        }
        return $this->tables[$table];
    }

    private function jsonError(int $code, string $message): void
    {
        http_response_code($code);
        header('Content-Type: application/json');
        echo json_encode(['error' => $message]);
        exit;
    }

    private function jsonResponse($data, int $code = 200): void
    {
        http_response_code($code);
        header('Content-Type: application/json');
        echo json_encode($data);
        exit;
    }

    public function index(string $table): void
    {
        $this->getTableConfig($table);
        $stmt = $this->db->query("SELECT * FROM {$table} ORDER BY id DESC");
        $this->jsonResponse($stmt->fetchAll());
    }

    public function show(string $table, $id): void
    {
        $this->getTableConfig($table);

        $stmt = $this->db->prepare("SELECT * FROM {$table} WHERE id = :id");
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch();

        if (!$row) {
            $this->jsonError(404, 'Not found');
        }

        $this->jsonResponse($row);
    }

    public function create(string $table): void
    {
        $config = $this->getTableConfig($table);

        $input = json_decode(file_get_contents('php://input'), true);

        if ($input === null) {
            $this->jsonError(400, 'Invalid JSON');
        }

        $this->validate($input, $config);

        $columns = array_intersect(array_keys($input), $config['columns']);
        $placeholders = array_map(fn($c) => ":{$c}", $columns);

        $sql = sprintf(
            "INSERT INTO %s (%s) VALUES (%s) RETURNING *",
            $table,
            implode(', ', $columns),
            implode(', ', $placeholders)
        );

        $stmt = $this->db->prepare($sql);
        foreach ($columns as $col) {
            $stmt->bindValue(":{$col}", $input[$col]);
        }
        $stmt->execute();

        $this->jsonResponse($stmt->fetch(), 201);
    }

    public function update(string $table, $id): void
    {
        $config = $this->getTableConfig($table);
        $input = json_decode(file_get_contents('php://input'), true);

        if ($input === null) {
            $this->jsonError(400, 'Invalid JSON');
        }

        $this->validate($input, $config, isUpdate: true);

        $columns = array_intersect(array_keys($input), $config['columns']);

        if (empty($columns)) {
            $this->jsonError(400, 'No valid fields to update');
        }

        $setClauses = array_map(fn($c) => "{$c} = :{$c}", $columns);

        $sql = sprintf(
            "UPDATE %s SET %s WHERE id = :id RETURNING *",
            $table,
            implode(', ', $setClauses)
        );

        $stmt = $this->db->prepare($sql);
        foreach ($columns as $col) {
            $stmt->bindValue(":{$col}", $input[$col]);
        }
        $stmt->bindValue(':id', $id);
        $stmt->execute();

        $row = $stmt->fetch();
        if (!$row) {
            $this->jsonError(404, 'Not found');
        }

        $this->jsonResponse($row);
    }

    public function delete(string $table, $id): void
    {
        $this->getTableConfig($table);
        $stmt = $this->db->prepare("DELETE FROM {$table} WHERE id = :id RETURNING id");
        $stmt->execute(['id' => $id]);

        if (!$stmt->fetch()) {
            $this->jsonError(404, 'Not found');
        }

        $this->jsonResponse(['deleted' => true]);
    }

    private function validate(array $input, array $config, bool $isUpdate = false): void
    {
        if (!$isUpdate) {
            foreach ($config['required'] ?? [] as $field) {
                if (!isset($input[$field]) || $input[$field] === '') {
                    $this->jsonError(422, "Missing required field: {$field}");
                }
            }
        }

        foreach ($config['allowed_values'] ?? [] as $field => $allowed) {
            if (isset($input[$field]) && !in_array($input[$field], $allowed, true)) {
                $this->jsonError(422, "Invalid value for {$field}: must be one of " . implode(', ', $allowed));
            }
        }
    }
}
