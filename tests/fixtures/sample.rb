require 'json'
require_relative './helper'

# WHY: sample fixture for Phase 9 Ruby extraction tests
# Covers: module, class, instance methods, singleton methods, bare calls

# A utility module for shared behavior
module Utils
  # NOTE: fmt converts values to strings for display
  def self.fmt(value)
    value.to_s
  end
end

# A person domain object
class Person
  # Initialize a new Person with name and age.
  # HACK: age validation should be extracted to a validator
  def initialize(name, age)
    @name = name
    @age = age
  end

  # Greet the person by name.
  def greet
    say_hello(@name)
  end

  # Factory class method to create a Person.
  def self.create(name)
    new(name, 0)
  end
end

# Top-level helper function
def say_hello(name)
  puts name
end
