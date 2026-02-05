# Handles in Vulkan-Hpp

The default handle types in Vulkan-Hpp are thin wrappers around the Vulkan C handles.
They provide type safety and convenience functions, but do not manage the lifetime of the underlying Vulkan resources.
To facilitate automatic resource management, Vulkan-Hpp provides two additional handle types: `vk::UniqueHandle` and `vk::SharedHandle`. Additionally, there are RAII-style classes in the `vk::raii` namespace that encapsulate resource management.

> [!NOTE]
> Note that none of the handles listed here are binary-compatible with the underlying Vulkan C handles.

- [`vk::UniqueHandle`](#vkuniquehandle)
- [`vk::SharedHandle`](#vksharedhandle)
- [`vk::raii`](#vkraii)
  - [General usage](#general-usage)
    - [Construction](#construction)
    - [Smart pointer management](#smart-pointer-management)
    - [Member functions](#member-functions)
  - [Step-by-step tutorial](#step-by-step-tutorial)
    - [Create a `vk::raii::Context`](#create-a-vkraiicontext)
    - [Create a `vk::raii::Instance`](#create-a-vkraiiinstance)
    - [Enumerate and filter `vk::raii::PhysicalDevices`](#enumerate-and-filter-vkraiiphysicaldevices)
    - [Create a `vk::raii::Device`](#create-a-vkraiidevice)
    - [Create a `vk::raii::CommandPool` and `vk::raii::CommandBuffers`](#create-a-vkraiicommandpool-and-vkraiicommandbuffers)
    - [Create a `vk::raii::SwapchainKHR`](#create-a-vkraiiswapchainkhr)
    - [Create a Depth Buffer](#create-a-depth-buffer)
    - [Create a Uniform Buffer](#create-a-uniform-buffer)
    - [Create a `vk::raii::PipelineLayout`](#create-a-vkraiipipelinelayout)
    - [Create a `vk::raii::DescriptorPool` and `vk::raii::DescriptorSets`](#create-a-vkraiidescriptorpool-and-vkraiidescriptorsets)
    - [Create a `vk::raii::RenderPass`](#create-a-vkraiirenderpass)
    - [Create a `vk::raii::ShaderModule`](#create-a-vkraiishadermodule)
    - [Create `vk::raii::Framebuffers`](#create-vkraiiframebuffers)
    - [Initialize a Vertex Buffer](#initialize-a-vertex-buffer)
    - [Initialize a Graphics Pipeline](#initialize-a-graphics-pipeline)
    - [Drawing a Cube](#drawing-a-cube)
  - [Conclusion](#conclusion)

## `vk::UniqueHandle`

Vulkan-Hpp provides a `vk::UniqueHandle<Type, Deleter>` interface.
This is a smart pointer similar to `std::unique_ptr`, which ensures that the underlying Vulkan handle is automatically destroyed when the `vk::UniqueHandle` goes out of scope.

Vulkan-Hpp defines aliases to this template for each Vulkan handle type.
Each `vk::Type` (or `VkType` in the C interface), has a corresponding `vk::UniqueType`.
That is, `vk::UniqueBuffer` is the unique handle for `vk::Buffer`.
For each function that constructs a `vk::Type`, there is a corresponding function that constructs `vk::UniqueType`.
For example, `vk::Device::createBuffer` maps to `vk::Device::createBufferUnique`, and `vk::allocateCommandBuffers` maps to `vk::allocateCommandBuffersUnique`.

> [!NOTE]
> `vk::UniqueHandle` is _not_ a 'zero-cost abstraction'.
> Most deleters have to store `vk::AllocationCallbacks` and the parent handle used for construction, which are required for automatic destruction on scope exit.
> For example, `vk::UniqueBuffer` stores a reference to the `vk::Device` used to create the buffer.
>
> This implies additional memory overhead, and function pointer chain dereferencing during destruction.

## `vk::SharedHandle`

Vulkan-Hpp provides a `vk::SharedHandle<Type>` interface.
This is a smart pointer similar to `std::shared_ptr`, which ensures that the underlying Vulkan handle is automatically destroyed when the last `vk::SharedHandle` referencing it goes out of scope.
Vulkan-Hpp defines aliases to this template for each Vulkan handle type.
For each Vulkan handle type `vk::Type` there is a shared handle `vk::SharedType` which will delete the underlying Vulkan resource upon destruction, e.g. `vk::SharedBuffer` is the shared handle for `vk::Buffer`.

Unlike `vk::UniqueHandle`, `vk::SharedHandle` takes shared ownership of the resource as well as its parent.
This means that the parent handle will not be destroyed until all child resources are deleted.
For instance, if a `vk::SharedBuffer` is created with a `vk::SharedDevice` as its parent, the `vk::SharedDevice` will not be destroyed until all `vk::SharedBuffer` instances created from it are destroyed.
This is useful for resources that are shared between multiple threads or objects.

> [!WARNING]
> Shared handles are not thread-safe.
> Multi-threaded access to the same `vk::SharedHandle` instance must be synchronised by the user.

This mechanism ensures correct destruction order even if destruction of the parent `vk::SharedHandle` is attempted before that of its child handle.
It follows that a `vk::SharedInstance` will be the last object to be destroyed in a Vulkan application using `vk::SharedHandle`s.

Functions which directly construct a `vk::SharedHandle` have not yet been implemented.
Instead, construct a `vk::SharedHandle` from a `vk::Handle`:

```c++
vk::Buffer buffer = device.createBuffer(...);
vk::SharedBuffer sharedBuffer(buffer, device); // sharedBuffer now owns the buffer
```

There are several specializations of `vk::SharedHandle` for different handle types. For example, `vk::SharedImage` may take an additional argument to specify if the image is owned by swapchain:

```c++
vk::Image image = swapchain.getImages(...)[0]; // get the first image from the swapchain
vk::SharedImage sharedImage(image, device, SwapChainOwns::yes); // sharedImage now owns the image, but won't destroy it
```

There is also a specialization for `vk::SwapchainKHR` which takes an additional argument to specify a surface:

```c++
vk::SwapchainKHR swapchain = device.createSwapchainKHR(...);
vk::SharedSwapchainKHR sharedSwapchain(swapchain, device, surface); // sharedSwapchain now owns the swapchain and surface
```

Create a `vk::SharedHandle` overload for custom handle types or shared handles by providing several template arguments to `SharedHandleBase`:

- A handle type
- A parent handle type or a header structure, that contains the parent
- A class itself for CRTP

With this, provide a custom static destruction function `internalDestroy`, that takes in a parent handle and a handle to destroy.
Add a `friend` declaration for the base class.

```c++
// Example of a custom shared device, that accepts an instance as a parent
class shared_handle<VkDevice> : public vk::SharedHandleBase<VkDevice, vk::SharedInstance, shared_handle<VkDevice>>
{
  using base = vk::SharedHandleBase<VkDevice, vk::SharedInstance, shared_handle<VkDevice>>;
  friend base;

public:
  shared_handle() = default;
  explicit shared_handle(VkDevice handle, vk::SharedInstance parent) noexcept
    : base(handle, std::move(parent)) {}

  const auto& getParent() const noexcept
  {
    return getHeader();
  }

protected:
  static void internalDestroy(const vk::SharedInstance& /*control*/, VkDevice handle) noexcept
  {
    kDestroyDevice(handle);
  }
};
```

Vulkan-Hpp will be extended to provide creation functions in the future.

## `vk::raii`

The `vk::raii` namespace, declared in `vulkan_raii.hpp` is an abstraction layer atop Vulkan-Hpp that follows the [RAII idiom](https://en.cppreference.com/w/cpp/language/raii.html).
The types in this namespace uses all Vulkan-Hpp enumerations and wrappers, and additionally provides a new set of wrapper classes for the Vulkan handle types, which use idiomatic C++ that wraps the construction and destruction functions; C++ constructors are used to create the underlying Vulkan resource, and destructors are used to destroy it.

`vk::UniqueHandle`, `vk::SharedHandle`, and `vk::Handle` types all use the same dispatcher, and these can be straightforwardly mixed.
To use them, initialise a global dispatcher as described in [Usage](./Usage.md#extensions-and-per-device-function-pointers).

`vk::raii` types have a custom dispatcher and are _not_ compatible with the aforementioned types, and maintain their own dispatchers.
With multiple devices in the same application, this is very useful as `vk::raii` member function calls will always be device-specific.

### General usage

#### Construction

To create a `vk::Device`, one might write:

```cpp
// create a vk::Device, given a `vk::PhysicalDevice physicalDevice` and a `vk::DeviceCreateInfo deviceCreateInfo`
vk::Device device = physicalDevice.createDevice( deviceCreateInfo );
```

and to destroy it:

```cpp
// destroy a vk::Device
device.destroy();
```

In comparison, to create the corresponding `vk::raii::Device` handle, use the constructor:

```cpp
// create a vk::raii::Device, given a `vk::raii::PhysicalDevice physicalDevice` and a `vk::DeviceCreateInfo deviceCreateInfo`
vk::raii::Device device( physicalDevice, deviceCreateInfo );
```

The created `device` object is automatically destroyed when execution leaves the the scope containing it.

Alternatively, use a creation function that returns the created object:

```cpp
// create a vk::raii::Device, given a `vk::raii::PhysicalDevice physicalDevice` and a `vk::DeviceCreateInfo deviceCreateInfo`
vk::raii::Device device = physicalDevice.createDevice( deviceCreateInfo );
```

If `VULKAN_HPP_NO_EXCEPTIONS` is defined, these creation functions match the signature and behaviour of other Vulkan-Hpp operations in [Usage § Error handling](./Usage.md/#error-handling).
`VULKAN_HPP_USE_STD_EXPECTED` is also supported.

For example, creating a `vk::raii::Device` might look like this:

```cpp
// when `VULKAN_HPP_NO_EXCEPTIONS` and `VULKAN_HPP_USE_STD_EXPECTED` are defined and C++23 is available
auto deviceExpected = physicalDevice.createDevice( deviceCreateInfo );
if ( deviceExpected.has_value() )
{
    device = std::move( *deviceExpected );
}
```

> [!NOTE]
> In this guide, the throwing constructors are used.

#### Smart pointer management

A `vk::raii::Device` object may be used directly as references, managed by C++ smart pointers, or other custom data structures.
They may even be allocated on the heap with `new`, and assigned to raw pointers.

For instance, with `std::unique_ptr`:

```cpp
std::unique_ptr<vk::raii::Device> pDevice = std::make_unique<vk::raii::Device>( *pPhysicalDevice, deviceCreateInfo );
```

All types in the `vk::raii` namespace directly contain and therefore own the underlying Vulkan resource.
These can be **moved**, but not copied.

In the rest of this guide, `vk::raii` objects are always instantiated directly on the stack.

> [!NOTE]
> For the most idiomatic usage, pass `vk::raii` objects as references (possibly `const`).

#### Member functions

Parallel to `vk::Handle`, `vk::raii::Handle` types provide member functions related to that class.
For instance, considering the wrappers and Vulkan-Hpp equivalents for calling `vkDeviceWaitIdle` for a `VkDevice`:

```cpp
// call `waitIdle` from a `vk::Device`
myVkDevice.waitIdle();

// call `waitIdle` from a `vk::raii::Device`
myVkRaiiDevice.waitIdle();
```

Additionally, `vk::raii` types have stronger correlations between handles and operations that can be performed on them.
In the `vk` namespace, most functions are members of `vk::Device`.
In the `vk::raii` namespace, functions strongly related to a non-dispatchable handle are members of the corresponding `vk::raii` object.

For example, compare binding some memory to a `vk::Buffer`...

```cpp
device.bindBufferMemory( /* vk::Buffer */ buffer, /* vk::DeviceMemory */ memory, /* vk::DeviceSize */ memoryOffset );
```

... And a `vk::raii::Buffer`:

```cpp
buffer.bindMemory( /* vk::DeviceMemory */ *memory, /* vk::DeviceSize */ memoryOffset );
```

> [!NOTE]
> `vk::raii::Buffer::bindMemory()` accepts an instance of `vk::DeviceMemory` as its first argument, and **not** `vk::raii::DeviceMemory`.
> Use `operator*()` to access the corresponding `vk::DeviceMemory` object from an instance of `vk::raii::DeviceMemory`.

### Step-by-step tutorial

Here, we will walk through an entire Vulkan application using `vk::raii` types, from instance creation to drawing a cube.

#### Create a `vk::raii::Context`

Unlike the rest of Vulkan or `Vulkan-Hpp`, the `vk::raii` namespace introduces a new class: `vk::raii::Context`.
This provides a handle to several functions that are not bound to a `VkInstance` or a `VkDevice`:

```cpp
// instantiate a vk::raii::Context
// No arguments are needed, as the context will load all the global function pointers on construction
vk::raii::Context context();

// get the API version, using that context
uint32_t apiVersion = context.enumerateInstanceVersion();
```

#### Create a `vk::raii::Instance`

Then, to construct an instance of `vk::raii::Instance`, use the above-created object `context` and a `vk::InstanceCreateInfo`:

```cpp
// instantiate a vk::raii::Instance
vk::raii::Instance instance( /* vk::raii::Context& */ context, /* vk::InstanceCreateInfo& */ instanceCreateInfo );
```

`instance` now holds all instance-related functions.
For example, to get all `vk::PhysicalDeviceGroupProperties` for an instance:

```cpp
std::vector<vk::PhysicalDeviceGroupProperties> physicalDeviceGroupProperties = instance.enumeratePhysicalDeviceGroups();
```

#### Enumerate and filter `vk::raii::PhysicalDevices`

Enumerating the physical devices of an instance is slightly different in `vk::raii` namespace compared to the `vk` namespace or the C API.
As there might be multiple physical devices attached, users should instantiate a `vk::raii::PhysicalDevices` (note the plural form of the word), which is a `std::vector` of **possibly multiple** `vk::raii::PhysicalDevice`s:

```cpp
vk::raii::PhysicalDevices physicalDevices( instance );
```

Just like any instance of `std::vector`, access any specific `vk::raii:PhysicalDevice` by indexing into that `std::vector`:

```cpp
std::vector<vk::LayerProperties> layerProperties = physicalDevices[/* size_t */ physicalDeviceIndex].enumerateDeviceLayerProperties();
```

To select just _one_ `vk::raii::PhysicalDevice`, use `std::move` to take ownership:

```cpp
// get the vk::raii::PhysicalDevice with index physicalDeviceIndex, given a vk::raii::PhysicalDevices physicalDevices object:
vk::raii::PhysicalDevice physicalDevice( std::move( physicalDevices[physicalDeviceIndex] ) );
```

#### Create a `vk::raii::Device`

Now, instantiate a `vk::raii::Device` using the above-created `vk::raii::PhysicalDevice` and a `vk::DeviceCreateInfo`:

```cpp
vk::raii::Device device(
  physicalDevice,      // vk::raii::PhysicalDevice&
  deviceCreateInfo     // vk::DeviceCreateInfo&
);
```

For each instantiated `vk::raii::Device`, device-specific Vulkan function pointers are resolved.
That is, for multi-device programs, each instance automatically uses its device-specific function pointers, and organizing a multi-device program is straightforward:

```cpp
// create a `vk::raii::Device` per `vk::raii::PhysicalDevice`, given a `vk::raii::PhysicalDevices physicalDevices`, and a corresponding array of `vk::DeviceCreateInfo deviceCreateInfos`
std::vector<vk::raii::Device> devices;
for ( size_t i = 0; i < physicalDevices.size(); i++ )
{
  devices.emplace_back( physicalDevices[i], deviceCreateInfos[i] );
}
```

#### Create a `vk::raii::CommandPool` and `vk::raii::CommandBuffers`

Instantiate a `vk::raii::CommandPool`:

```cpp
vk::raii::CommandPool commandPool(
  device,                    // vk::raii::Device&
  commandPoolCreateInfo      // vk::CommandPoolCreateInfo&
);
```

As the number of `vk::raii::CommandBuffer`s to allocate from a `vk::raii::CommandPool` is given by the member `commandBufferCount` of a `vk::CommandBufferAllocateInfo` structure, it can't be instantiated as a single object.
Instead you get a `vk::raii::CommandBuffers` (note the plural form), which essentially is a `std::vector` of `vk::raii::CommandBuffer`s (note the trailing 's' here!).

```cpp
// create a vk::raii::CommandBuffers, given a vk::raii::Device device and a vk::CommandBufferAllocateInfo commandBufferAllocateInfo
vk::raii::CommandBuffers commandBuffers( device, commandBufferAllocateInfo );
```

Note, that the `vk::CommandBufferAllocateInfo` holds a `vk::CommandPool` member `commandPool`. To assign that from a `vk::raii::CommandPool` you can use the `operator*()`:

```cpp
// assign vk::CommandBufferAllocateInfo::commandPool, given a vk::raii::CommandPool commandPool
commandBufferAllocateInfo.commandPool = *commandPool;
```

As a `vk::raii::CommandBuffers` is just a `std::vector<vk::raii::CommandBuffer>`, you can access any specific `vk::raii:CommandBuffer` by indexing into that `std::vector`:

```cpp
// start recording of the vk::raii::CommandBuffer with index commandBufferIndex, given a vk::raii::CommandBuffers commandBuffers
commandBuffers[commandBufferIndex].begin();
```

You can as well get one `vk::raii::CommandBuffer` out of a `vk::raii::CommandBuffers` like this:

```cpp
// get the vk::raii::CommandBuffer with index commandBufferIndex, given a vk::raii::CommandBuffers commandBuffers
vk::raii::CommandBuffer commandBuffer( std::move( commandBuffers[commandBufferIndex] ) );

// start recording
commandBuffer.begin();
```

There is one important thing to note, regarding command pool and command buffer handling. When you destroy a `VkCommandPool`, all `VkCommandBuffer`s allocated from that pool are implicitly freed. That automatism does not work well with the raii-approach. As the `vk::raii::CommandBuffers` are independent objects, they are not automatically destroyed when the `vk::raii::CommandPool` they are created from is destroyed. Instead, their destructor would try to use an invalid `vk::raii::CommandPool`, which obviously is an error.

To handle that correctly, you have to make sure, that all `vk::raii::CommandBuffers` generated from a `vk::raii::CommandPool` are explicitly destroyed before that `vk::raii::CommandPool` is destroyed!

#### Create a `vk::raii::SwapchainKHR`

To initialize a swap chain, you first instantiate a `vk::raii::SwapchainKHR`:

```cpp
// create a vk::raii::SwapchainKHR, given a vk::raii::Device device and a vk::SwapchainCreateInfoKHR swapChainCreateInfo
vk::raii::SwapchainKHR swapchain( device, swapChainCreateInfo );
```

You can get an array of presentable images associated with that swap chain:

```cpp
// get presentable images associated with vk::raii::SwapchainKHR swapchain
std::vector<VkImage> images = swapchain.getImages();
```

Note, that you don't get `vk::raii::Image`s here, but plain `VkImage`s. They are controlled by the swap chain, and you should not destroy them.

But you can create `vk::raii::ImageView`s out of them:

```cpp
// create a vk::raii::ImageView per VkImage, given a vk::raii::Device sevice, a vector of VkImages images and a vk::ImageViewCreateInfo imageViewCreateInfo
std::vector<vk::raii::ImageView> imageViews;
for ( auto image : images )
{
  imageViewCreatInfo.image = image;
  imageViews.push_back( vk::raii::ImageView( device, imageViewCreateInfo ) );
}
```

#### Create a Depth Buffer

For a depth buffer, you need an image and some device memory and bind the memory to that image. That is, you first create a vk::raii::Image

```cpp
// create a vk::raii::Image image, given a vk::raii::Device device and a vk::ImageCreateInfo imageCreateInfo
// imageCreateInfo.usage should hold vk::ImageUsageFlagBits::eDepthStencilAttachment
vk::raii::Image depthImage( device, imageCreateInfo );
```

To create the corresponding vk::raii::DeviceMemory, you should determine appropriate values for the vk::MemoryAllocateInfo. That is, get the memory requirements from the pDepthImage, and determine some memoryTypeIndex from the pPhysicalDevice's memory properties, requiring vk::MemoryPropertyFlagBits::eDeviceLocal.

```cpp
// get the vk::MemoryRequirements of the pDepthImage
vk::MemoryRequirements memoryRequirements = depthImage.getMemoryRequirements();

// determine appropriate memory type index, using some helper function determineMemoryTypeIndex
vk::PhysicalDeviceMemoryProperties memoryProperties = physicalDevice.getMemoryProperties();
uint32_t memoryTypeIndex = determineMemoryTypeIndex( memoryProperties, memoryRequirements.memoryTypeBits, vk::MemoryPropertyFlagBits::eDeviceLocal );

// create a vk::raii::DeviceMemory depthDeviceMemory for the depth buffer
vk::MemoryAllocateInfo memoryAllocateInfo( memoryRequirements.size, memoryTypeIndex );
vk::raii::DeviceMemory depthDeviceMemory( device, memoryAllocateInfo );
```

Then you can bind the depth memory to the depth image

```cpp
// bind the pDepthMemory to the pDepthImage
depthImage.bindMemory( *depthDeviceMemory, 0 );
```

Finally, you can create an image view on that depth buffer image

```cpp
// create a vk::raii::ImageView depthView, given a vk::ImageViewCreateInfo imageViewCreateInfo
imageViewCreateInfo.image = *depthImage;
vk::raii::ImageView depthImageView( device, imageViewCreateInfo );
```

#### Create a Uniform Buffer

Initializing a uniform buffer is very similar to initializing a depth buffer as described above. You just instantiate a `vk::raii::Buffer` instead of a `vk::raii::Image`, and a `vk::raii::DeviceMemory`, and bind the memory to the buffer:

```cpp
// create a vk::raii::Buffer, given a vk::raii::Device device and a vk::BufferCreateInfo bufferCreateInfo
vk::raii::Buffer uniformBuffer( device, bufferCreateInfo );

// get memoryRequirements for this uniform buffer
vk::MemoryRequirements memoryRequirements = uniformBuffer.getMemoryRequirements();

// determine appropriate memory type index, using some helper function, given a vk::raii::PhysicalDevice physicalDevice and some memoryPropertyFlags
vk::PhysicalDeviceMemoryProperties memoryProperties = physicalDevice.getMemoryProperties();
uint32_t memoryTypeIndex = determineMemoryTypeIndex( memoryProperties, memoryRequirements.memoryTypeBits, memoryPropertyFlags );

// create a vk::raii::DeviceMemory uniformDeviceMemory for the uniform buffer
vk::MemoryAllocateInfo memoryAllocateInfo( memoryRequirements.size, memoryTypeIndex );
vk::raii::DeviceMemory uniformDeviceMemory( device, memoryAllocateInfo );

// bind the vk::raii::DeviceMemory uniformDeviceMemory to the vk::raii::Buffer uniformBuffer
uniformBuffer.bindMemory( *uniformDeviceMemory, 0 );
```

#### Create a `vk::raii::PipelineLayout`

To initialize a Pipeline Layout you just have to instantiate a `vk::raii::DescriptorSetLayout` and a `vk::raii::PipelineLayout` using that `vk::raii::DescriptorSetLayout`:

```cpp
// create a vk::raii::DescriptorSetLayout, given a vk::raii::Device device and a vk::DescriptorSetLayoutCreateInfo descriptorSetLayoutCreateInfo
vk::raii::DescriptorSetLayout descriptorSetLayout( device, descriptorSetLayoutCreateInfo );

// create a vk::raii::PipelineLayout, given a vk::raii::Device device and a vk::raii::DescriptorSetLayout
vk::PipelineLayoutCreateInfo pipelineLayoutCreateInfo( {}, *descriptorSetLayout );
vk::raii::PipelineLayout pipelineLayout( device, pipelineLayoutCreateInfo );
```

#### Create a `vk::raii::DescriptorPool` and `vk::raii::DescriptorSets`

The Descriptor Set handling with `vk::raii` requires some special handling that is not needed when using the pure C-API or the vk-namespace!

As a `vk::raii::DescriptorSet` object destroys itself in the destructor, you have to instantiate the corresponding `vk::raii::DescriptorPool` with the `vk::DescriptorPoolCreateInfo::flags` set to (at least) `vk::DescriptorPoolCreateFlagBits::eFreeDescriptorSet`. Otherwise, such individual destruction of a `vk::raii::DescriptorSet` would not be allowed!

That is, an instantiation of a `vk::raii::DescriptorPool` would look like this:

```cpp
// create a vk::raii::DescriptorPool, given a vk::raii::Device device and a vk::DescriptorPoolCreateInfo descriptorPoolCreateInfo
assert( descriptorPoolCreateInfo.flags & vk::DescriptorPoolCreateFlagBits::eFreeDescriptorSet );
vk::raii::DescriptorPool descriptorPool( device, descriptorPoolCreateInfo );
```

To actually instantiate a `vk::raii::DescriptorSet`, you need a `vk::raii::DescriptorPool`, as just described, and a `vk::raii::DescriptorSetLayout`, similar to the one described in the previous section.

Moreover, as the number of `vk::raii::DescriptorSet`s to allocate from a `vk::raii::DescriptorPool` is given by the number of `vk::DescriptorSetLayouts` held by a `vk::DescriptorSetAllocateInfo`, it can't be instantiated as a single object. Instead you get a `vk::raii::DescriptorSets` (note the trailing 's' here!), which essentially is a `std::vector` of `vk::raii::DescriptorSet`s (note the trailing 's' here!).

When you want to create just one `vk::raii::DescriptorSet`, using just one `vk::raii::DescriptorSetLayout`, your code might look like this:

```cpp
// create a vk::raii::DescriptorSets, holding a single vk::raii::DescriptorSet, given a vk::raii::Device device, a vk::raii::DescriptorPool descriptorPool, and a single vk::raii::DescriptorSetLayout descriptorSetLayout
vk::DescriptorSetAllocateInfo descriptorSetAllocateInfo( *descriptorPool, *descriptorSetLayout );
vk::raii::DescriptorSets pDescriptorSets( device, descriptorSetAllocateInfo );
```

And, again similar to the vk::raii::CommandBuffers handling described above, you can get one `vk::raii::DescriptorSet` out of a `vk::raii::DescriptorSets` like this:

```cpp
// get the vk::raii::DescriptorSet with index descriptorSetIndex, given a vk::raii::DescriptorSets descriptorSets
vk::raii::DescriptorSet descriptorSet( std::move( descriptorSets[descriptorSetIndex] ) );
```

#### Create a `vk::raii::RenderPass`

Creating a `vk::raii::RenderPass` is pretty simple, given you already have a meaningful `vk::RenderPassCreateInfo`:

```cpp
// create a vk::raii::RenderPass, given a vk::raii::Device device and a vk::RenderPassCreateInfo renderPassCreateInfo
vk::raii::RenderPass renderPass( device, renderPassCreateInfo );
```

#### Create a `vk::raii::ShaderModule`

Again, creating a `vk::raii::ShaderModule` is simple, given a `vk::ShaderModuleCreateInfo` with some meaningful code:

```cpp
// create a vk::raii::ShaderModule, given a vk::raii::Device device and a vk::ShaderModuleCreateInfo shaderModuleCreateInfo
vk::raii::ShaderModule shaderModule( device, shaderModuleCreateInfo );
```

#### Create `vk::raii::Framebuffers`

If you have a `std::vector<vk::raii::ImageView>` as described in chapter 05 above, with one view per `VkImage` that you got from a `vk::raii::SwapchainKHR`; and one `vk::raii::ImageView` as described in chapter 06 above, which is a view on a `vk::raii::Image`, that is supposed to be a depth buffer, you can create a `vk::raii::Framebuffer` per swapchain image.

```cpp
// create a vector of vk::raii::Framebuffer, given a vk::raii::ImageView depthImageView, a vector of vk::raii::ImageView swapchainImageViews, a vk::raii::RenderPass renderPass, a vk::raii::Devie device, and some width and height
// use the depth image view as the second attachment for each vk::raii::Framebuffer
std::array<vk::ImageView, 2> attachments;
attachments[1] = *depthImageView;
std::vector<vk::raii::Framebuffer> framebuffers;
for ( auto const & imageView : swapchainImageViews )
{
  // use each image view from the swapchain as the first attachment
  attachments[0] = *imageView;
  vk::FramebufferCreateInfo framebufferCreateInfo( {}, *renderPass, attachments, width, height, 1 );
  framebuffers.push_back( vk::raii::Framebuffer( device, framebufferCreateInfo ) );
}
```

#### Initialize a Vertex Buffer

To initialize a vertex buffer, you essentially have to combine some of the pieces described in the chapters before. First, you need to create a `vk::raii::Buffer` and a `vk::raii::DeviceMemory` and bind them:

```cpp
// create a vk::raii::Buffer vertexBuffer, given a vk::raii::Device device and some vertexData in host memory
vk::BufferCreateInfo bufferCreateInfo( {}, sizeof( vertexData ), vk::BufferUsageFlagBits::eVertexBuffer );
vk::raii::Buffer vertexBuffer( device, bufferCreateInfo );

// create a vk::raii::DeviceMemory vertexDeviceMemory, given a vk::raii::Device device and a uint32_t memoryTypeIndex
vk::MemoryRequirements memoryRequirements = vertexBuffer.getMemoryRequirements();
vk::MemoryAllocateInfo memoryAllocateInfo( memoryRequirements.size, memoryTypeIndex );
vk::raii::DeviceMemory vertexDeviceMemory( device, memoryAllocateInfo );

// bind the complete device memory to the vertex buffer
vertexBuffer.bindMemory( *vertexDeviceMemory, 0 );

// copy the vertex data into the vertexDeviceMemory
...
```

Later on, you can bind that vertex buffer to a command buffer:

```cpp
// bind a complete single vk::raii::Buffer vertexBuffer as a vertex buffer, given a vk::raii::CommandBuffer commandBuffer
commandBuffer.bindVertexBuffer( 0, { *vertexBuffer }, { 0 } );
```

#### Initialize a Graphics Pipeline

Initializing a graphics pipeline is not very raii-specific. Just instantiate it, provided you have a valid vk::GraphicsPipelineCreateInfo:

```cpp
// create a vk::raii::Pipeline, given a vk::raii::Device device and a vk::GraphicsPipelineCreateInfo graphicsPipelineCreateInfo
vk::raii::Pipeline graphicsPipeline( device, graphicsPipelineCreateInfo );
```

The only thing to keep in mind here is the dereferencing of raii handles, like `pipelineLayout` or `renderPass` in the `vk::GraphicsPipelineCreateInfo`:

```cpp
vk::GraphicsPipelineCreateInfo graphicsPipelineCreateInfo(
  {},                                    // flags
  pipelineShaderStageCreateInfos,        // stages
  &pipelineVertexInputStateCreateInfo,   // pVertexInputState
  &pipelineInputAssemblyStateCreateInfo, // pInputAssemblyState
  nullptr,                               // pTessellationState
  &pipelineViewportStateCreateInfo,      // pViewportState
  &pipelineRasterizationStateCreateInfo, // pRasterizationState
  &pipelineMultisampleStateCreateInfo,   // pMultisampleState
  &pipelineDepthStencilStateCreateInfo,  // pDepthStencilState
  &pipelineColorBlendStateCreateInfo,    // pColorBlendState
  &pipelineDynamicStateCreateInfo,       // pDynamicState
  *pipelineLayout,                       // layout
  *renderPass                            // renderPass
);
```

#### Drawing a Cube

Finally, we get all those pieces together and draw a cube.

To do so, you need a `vk::raii::Semaphore`:

```cpp
// create a vk::raii::Semaphore, given a vk::raii::Device
vk::raii::Semaphore imageAcquiredSemphore( device, vk::SemaphoreCreateInfo() );
```

That semaphore can be used, to acquire the next imageIndex from the `vk::raii::SwapchainKHR` swapchain:

```cpp
vk::Result result;
uint32_t imageIndex;
std::tie( result, imageIndex ) = swapchain.acquireNextImage( timeout, *imageAcquiredSemaphore );
```

Note, `vk::raii::SwapchainKHR::acquireNextImage` returns a `ResultValue<uint32_t>`, that can nicely be assigned onto two separate values using std::tie().

And also note, the returned `vk::Result` can not only be `vk::Result::eSuccess`, but also `vk::Result::eTimeout`, `vk::Result::eNotReady`, or `vk::Result::eSuboptimalKHR`, which should be handled here accordingly!

Next, you can record some commands into a `vk::raii::CommandBuffer`:

```cpp
// open the commandBuffer for recording
commandBuffer.begin( {} );

// initialize a vk::RenderPassBeginInfo with the current imageIndex and some appropriate renderArea and clearValues
vk::RenderPassBeginInfo renderPassBeginInfo( *renderPass, *framebuffers[imageIndex], renderArea, clearValues );

// begin the render pass with an inlined subpass; no secondary command buffers allowed
commandBuffer.beginRenderPass( renderPassBeginInfo, vk::SubpassContents::eInline );

// bind the graphics pipeline
commandBuffer.bindPipeline( vk::PipelineBindPoint::eGraphics, *graphicsPipeline );

// bind an appropriate descriptor set
commandBuffer.bindDescriptorSets( vk::PipelineBindPoint::eGraphics, *pipelineLayout, 0, { *descriptorSet }, nullptr );

// bind the vertex buffer
commandBuffer.bindVertexBuffers( 0, { *vertexBuffer }, { 0 } );

// set viewport and scissor
commandBuffer.setViewport( 0, viewport );
commandBuffer.setScissor( renderArea );

// draw the 12 * 3 vertices once, starting with vertex 0 and instance 0
commandBuffer.draw( 12 * 3, 1, 0, 0 );

// end the render pass and stop recording
commandBuffer.endRenderPass();
commandBuffer.end();
```

To submit that command buffer to a `vk::raii::Queue` graphicsQueue you might want to use a `vk::raii::Fence`

```cpp
// create a vk::raii::Fence, given a vk::raii::Device device
vk::raii::Fence fence( device, vk::FenceCreateInfo() );
```

With that, you can fill a `vk::SubmitInfo` and submit the command buffer

```cpp
vk::PipelineStageFlags waitDestinationStageMask( vk::PipelineStageFlagBits::eColorAttachmentOutput );
vk::SubmitInfo submitInfo( *imageAcquiredSemaphore, waitDestinationStageMask, *commandBuffer );
graphicsQueue.submit( submitInfo, *fence );
```

At some later point, you can wait for that submit being ready by waiting for the fence

```cpp
while ( vk::Result::eTimeout == device.waitForFences( { *fence }, VK_TRUE, timeout ) )
  ;
```

And finally, you can use the `vk::raii::Queue` presentQueue to, well, present that image

```cpp
vk::PresentInfoKHR presentInfoKHR( nullptr, *swapChain, imageIndex );
result = presentQueue.presentKHR( presentInfoKHR );
```

Note here, again, that `result` can not only be `vk::Result::eSuccess`, but also `vk::Result::eSuboptimalKHR`, which should be handled accordingly.

### Conclusion

With the vk::raii namespace you've got a complete set of Vulkan handle wrapper classes following the RAII-paradigm. That is, they can easily be assigned to a smart pointer. And you can't miss their destruction.

Moreover, the actual function pointer handling is done automatically by `vk::raii::Context`, `vk::raii::Instance`, and `vk::raii::Device`. That is, you always use the correct device-specific functions, no matter how many devices you're using.

Note, though, that there are a few classes, like `vk::raii::CommandPool` and `vk::raii::DescriptorSet`, that need some special handling that deviates from what you can do with the pure C-API or the wrapper classes in the vk-namespace.
