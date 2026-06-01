/**
 * Sample TypeScript file for parser tests.
 * Contains functions, classes, interfaces, and imports.
 */

import { readFileSync } from "fs";
import path from "path";

const CONSTANT = "hello";

/** Add two numbers and return the result. */
function standaloneFunction(x: number, y: number): number {
  return x + y;
}

function functionNoJsdoc(name: string): string {
  return `Hello, ${name}`;
}

interface SampleInterface {
  id: number;
  name: string;
  process(): void;
}

type SampleType = {
  value: string;
  count: number;
};

/** A sample class with multiple methods. */
class SampleClass implements SampleInterface {
  public id: number;
  public name: string;

  constructor(id: number, name: string) {
    this.id = id;
    this.name = name;
  }

  /** Execute the processing logic. */
  process(): void {
    console.log(`Processing ${this.name}`);
  }

  static create(id: number): SampleClass {
    return new SampleClass(id, "default");
  }
}

function callsOtherFunctions(): number | null {
  /** Demonstrates call edges to standaloneFunction. */
  const result = standaloneFunction(1, 2);
  const filePath = path.join(".", "sample.ts");
  const _content = readFileSync(filePath);
  return result > 0 ? result : null;
}

export { standaloneFunction, SampleClass, SampleInterface, SampleType };
