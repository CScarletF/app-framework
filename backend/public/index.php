<?php

require_once __DIR__ . '/../vendor/autoload.php';
require_once __DIR__ . '/../src/Database.php';
require_once __DIR__ . '/../src/Router.php';
require_once __DIR__ . '/../src/CrudController.php';

if (file_exists(__DIR__ . '/../.env')) {
    $lines = file(__DIR__ . '/../.env', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    foreach ($lines as $line) {
        if (str_starts_with(trim($line), '#')) continue;
        [$key, $value] = array_pad(explode('=', $line, 2), 2, '');
        putenv(trim($key) . '=' . trim($value));
    }
}

$db = Database::getConnection();
$tables = require __DIR__ . '/../src/Config/tables.php';
$controller = new CrudController($db, $tables);

$router = new Router();

$router->get('/api/{table}', function ($table) use ($controller) {
    $controller->index($table);
});

$router->get('/api/{table}/{id}', function ($table, $id) use ($controller) {
    $controller->show($table, $id);
});

$router->post('/api/{table}', function ($table) use ($controller) {
    $controller->create($table);
});

$router->put('/api/{table}/{id}', function ($table, $id) use ($controller) {
    $controller->update($table, $id);
});

$router->delete('/api/{table}/{id}', function ($table, $id) use ($controller) {
    $controller->delete($table, $id);
});

$router->run();
