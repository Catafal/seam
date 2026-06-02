<?php

namespace App\Http\Controllers;

use App\Models\User;
use App\Services\Logger;

/**
 * UserController handles user HTTP requests.
 * WHY: centralizes all user-related HTTP actions in one place
 */
class UserController
{
    // NOTE: property injected via constructor promotion in PHP 8
    private string $name = '';

    /**
     * List all users.
     */
    #[Route('/users')]
    public function index(): array
    {
        $result = getUsers();
        return $result;
    }

    // HACK: should use a proper repository pattern here
    protected function findUser(int $id): ?User
    {
        return null;
    }
}

interface Loggable
{
    /**
     * Write a log message.
     */
    public function log(string $msg): void;
}

trait HasTimestamps
{
    public function touch(): void {}
}

enum Status
{
    case Active;
    case Inactive;
}

/**
 * Get all users from storage.
 */
function getUsers(): array
{
    return [];
}
