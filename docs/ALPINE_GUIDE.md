# Alpine.js Usage Guide

This project uses Alpine.js for reactive components and state management. This guide explains how Alpine.js is used throughout the application.

## What is Alpine.js?

Alpine.js is a minimal JavaScript framework that provides reactive and declarative behavior directly in your HTML. Think of it as "Tailwind for JavaScript" - it uses directives in your HTML to add interactivity.

## Installation

Alpine.js is loaded from CDN in `base.html`:

```html
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.13.3/dist/cdn.min.js"></script>
```

No build step or npm install required!

## Core Concepts

### x-data

Defines a component's reactive state:

```html
<div x-data="{ open: false }">
  <!-- Component scope -->
</div>
```

### x-show / x-if

Conditionally show elements:

```html
<div x-show="open">I'm visible when open is true</div>
```

### x-model

Two-way data binding for inputs:

```html
<input type="text" x-model="searchQuery">
```

### x-bind (or :)

Bind attributes to state:

```html
<div :class="{ 'active': isActive }">
<button :disabled="loading">
```

### x-on (or @)

Event listeners:

```html
<button @click="open = true">Open</button>
<form @submit.prevent="handleSubmit()">
```

### x-transition

Smooth transitions:

```html
<div 
  x-show="open"
  x-transition:enter="transition ease-out duration-300"
  x-transition:enter-start="opacity-0"
  x-transition:enter-end="opacity-100"
>
  Animated content
</div>
```

## How It's Used in This Project

### 1. Modals (index.html)

**State:**
```html
<div x-data="{ showAddLibrary: false }">
```

**Open Button:**
```html
<button @click="showAddLibrary = true">Add Library</button>
```

**Modal:**
```html
<div 
  x-show="showAddLibrary"
  @click.self="showAddLibrary = false"
  @keydown.escape.window="showAddLibrary = false"
>
  <!-- Modal content -->
</div>
```

**Benefits:**
- No JavaScript functions needed
- Automatic ESC key handling
- Click outside to close
- Smooth transitions

### 2. Tabs (continue_reading.html)

**State:**
```html
<div x-data="{ currentFilter: 'in_progress' }">
```

**Tab Buttons:**
```html
<button 
  :class="{ 'active': currentFilter === 'in_progress' }"
  @click="currentFilter = 'in_progress'"
>
  In Progress
</button>
```

**Benefits:**
- Active state automatically managed
- No manual DOM manipulation
- Clean, declarative syntax

### 3. Forms (search.html)

**State:**
```html
<div x-data="{
  searchQuery: '',
  publisher: '',
  searching: false
}">
```

**Inputs:**
```html
<input type="text" x-model="searchQuery">
<select x-model="publisher">
```

**Submit:**
```html
<form @submit.prevent="performSearch()">
  <button :disabled="searching">
    <span x-show="!searching">Search</span>
    <span x-show="searching">Searching...</span>
  </button>
</form>
```

**Benefits:**
- Form state automatically tracked
- Easy validation
- Loading states
- No manual input value reading

### 4. View Modes (index.html)

**State:**
```html
<div x-data="{ viewMode: 'grid' }">
```

**Toggle Button:**
```html
<button @click="viewMode = viewMode === 'grid' ? 'list' : 'grid'">
  <span x-text="viewMode === 'grid' ? 'List View' : 'Grid View'"></span>
</button>
```

**Container:**
```html
<div :class="viewMode === 'grid' ? 'grid ...' : 'space-y-2'">
```

**Benefits:**
- Toggle between views with one click
- Classes automatically updated
- Button text automatically changes

### 5. Mobile Menu (base.html)

**State:**
```html
<body x-data="{ mobileMenuOpen: false }">
```

**Menu Button:**
```html
<button @click="mobileMenuOpen = !mobileMenuOpen">
  <path x-show="!mobileMenuOpen" d="..." />
  <path x-show="mobileMenuOpen" d="..." />
</button>
```

**Mobile Menu:**
```html
<div 
  x-show="mobileMenuOpen"
  x-transition:enter="transition ease-out duration-200"
>
  <!-- Menu items -->
</div>
```

**Benefits:**
- Smooth animations
- Icon automatically switches
- Clean mobile experience

## Integration with HTMX

Alpine.js works seamlessly with HTMX. Example:

```html
<div x-data="{ currentFilter: 'in_progress' }">
  <button @click="
    currentFilter = 'recent';
    htmx.ajax('GET', '/api/progress/?filter=recent', {
      target: '#list',
      swap: 'innerHTML'
    })
  ">
    Recent
  </button>
</div>
```

Alpine manages state, HTMX handles server communication.

## Accessing Alpine State from JavaScript

Use `Alpine.$data()` to access component state:

```javascript
const alpineData = Alpine.$data(document.querySelector('[x-data]'));
alpineData.showModal = true;
alpineData.searchQuery = 'Batman';
```

This is used in several places where vanilla JS needs to interact with Alpine state.

## Common Patterns

### 1. Modal Pattern

```html
<div x-data="{ open: false }">
  <button @click="open = true">Open</button>
  <div 
    x-show="open" 
    @click.self="open = false"
    @keydown.escape.window="open = false"
    x-transition
  >
    <div>
      Modal content
      <button @click="open = false">Close</button>
    </div>
  </div>
</div>
```

### 2. Tab Pattern

```html
<div x-data="{ tab: 'first' }">
  <button @click="tab = 'first'" :class="{ 'active': tab === 'first' }">
    First
  </button>
  <button @click="tab = 'second'" :class="{ 'active': tab === 'second' }">
    Second
  </button>
  
  <div x-show="tab === 'first'">First content</div>
  <div x-show="tab === 'second'">Second content</div>
</div>
```

### 3. Form Pattern

```html
<div x-data="{ 
  name: '', 
  email: '',
  loading: false 
}">
  <form @submit.prevent="submit()">
    <input type="text" x-model="name">
    <input type="email" x-model="email">
    <button :disabled="loading">
      <span x-show="!loading">Submit</span>
      <span x-show="loading">Loading...</span>
    </button>
  </form>
</div>
```

### 4. Toggle Pattern

```html
<div x-data="{ expanded: false }">
  <button @click="expanded = !expanded">
    <span x-text="expanded ? 'Hide' : 'Show'"></span>
  </button>
  <div x-show="expanded" x-transition>
    Hidden content
  </div>
</div>
```

## Best Practices

### 1. Keep State Local

```html
<!-- Good: Local state -->
<div x-data="{ open: false }">
  <button @click="open = true">Open</button>
</div>

<!-- Avoid: Global state unless needed -->
```

### 2. Use x-cloak for Flash Prevention

```html
<style>
  [x-cloak] { display: none !important; }
</style>

<div x-data="..." x-cloak>
  <!-- Won't flash before Alpine loads -->
</div>
```

### 3. Combine with HTMX

```html
<!-- Alpine for UI state, HTMX for server communication -->
<div x-data="{ filter: 'all' }">
  <button 
    @click="filter = 'completed'; htmx.ajax(...)"
    :class="{ 'active': filter === 'completed' }"
  >
    Completed
  </button>
</div>
```

### 4. Use x-model for Forms

```html
<!-- Good: Automatic two-way binding -->
<input type="text" x-model="query">

<!-- Avoid: Manual value tracking -->
<input type="text" @input="query = $event.target.value">
```

## Debugging

### View Component State

```javascript
// In browser console
Alpine.$data(document.querySelector('[x-data]'))
```

### Watch State Changes

```html
<div x-data="{ count: 0 }" x-effect="console.log('count:', count)">
  <button @click="count++">Increment</button>
</div>
```

## Resources

- [Alpine.js Documentation](https://alpinejs.dev/)
- [Alpine.js Cheat Sheet](https://www.alpine-cheatsheet.com/)
- [Alpine.js Examples](https://alpinejs.dev/examples)

## Summary

Alpine.js in this project provides:
- ✅ Reactive modal dialogs
- ✅ Tab switching with active states
- ✅ Form state management
- ✅ View mode toggling
- ✅ Mobile menu with transitions
- ✅ Loading states
- ✅ All with minimal JavaScript

The combination of HTMX (server communication) + Alpine.js (UI state) + Tailwind (styling) creates a modern, reactive frontend with no build step required!
