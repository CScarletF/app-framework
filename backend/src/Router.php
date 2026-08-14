<?php

require_once __DIR__ . '/../vendor/autoload.php';

use Bramus\Router\Router as BramusRouter;

class Router
{
    private BramusRouter $router;

    public function __construct()
    {
        $this->router = new BramusRouter();
    }

    public function get(string $pattern, callable $callback): void
    {
        $this->router->get($pattern, $callback);
    }

    public function post(string $pattern, callable $callback): void
    {
        $this->router->post($pattern, $callback);
    }

    public function put(string $pattern, callable $callback): void
    {
        $this->router->put($pattern, $callback);
    }

    public function delete(string $pattern, callable $callback): void
    {
        $this->router->delete($pattern, $callback);
    }

    public function run(): void
    {
        $this->router->set404(function () {
            http_response_code(404);
            header('Content-Type: application/json');
            echo json_encode(['error' => 'Not found']);
        });

        $this->router->run();
    }
}
